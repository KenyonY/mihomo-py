"""Linux TUN policy, read-only diagnostics and observed resource ownership.

Only mihomo creates/removes links and routes. iproute2 is used for inspection.
"""

import copy
import functools
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

from .errors import AppError

DEFAULT_TUN = {"enabled": False, "device": "mihomo", "stack": "mips", "exclude_address": []}
STACKS = ("mips", "system", "gvisor", "mixed")
TABLE = 2022
RULE_START = 9000
RULE_END = 9020  # Reserve the full core rule range, including terminating rules.
DNS_HIJACK = ["any:53", "tcp://any:53"]


def validate(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_TUN):
        raise AppError("invalid_settings", "TUN 设置格式无效。", 2, "恢复设置备份。")
    if type(value["enabled"]) is not bool:
        raise AppError(
            "invalid_settings", "TUN enabled 必须为布尔值。", 2, "使用 tun enable/disable。"
        )
    if (
        not isinstance(value["device"], str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,15}", value["device"])
        or value["device"] in (".", "..")
    ):
        raise AppError(
            "tun_invalid_device",
            "TUN 设备名无效。",
            2,
            "使用 1–15 个字母、数字、点、下划线或连字符。",
        )
    if value["stack"] not in STACKS:
        raise AppError(
            "tun_invalid_stack", "TUN 协议栈无效。", 2, "使用 mips、system、gvisor 或 mixed。"
        )
    if not isinstance(value["exclude_address"], list):
        raise AppError(
            "tun_invalid_address",
            "排除地址必须是列表。",
            2,
            "使用 --exclude-address IP/CIDR，可重复指定。",
        )
    try:
        for address in value["exclude_address"]:
            if not isinstance(address, str):
                raise ValueError
            ipaddress.ip_network(address, strict=False)
    except ValueError as exc:
        raise AppError(
            "tun_invalid_address",
            "排除地址必须是 IP 或 CIDR。",
            2,
            "例如 --exclude-address 192.168.1.0/24。",
        ) from exc
    return value


def normalized(value):
    result = copy.deepcopy(validate(value))
    result["exclude_address"] = list(
        dict.fromkeys(
            str(ipaddress.ip_network(address, strict=False))
            for address in result["exclude_address"]
        )
    )
    return result


def configuration(settings):
    value = settings.get("tun", DEFAULT_TUN)
    if not value["enabled"]:
        return {"enable": False}
    return {
        "enable": True,
        "device": value["device"],
        "stack": value["stack"],
        "auto-route": True,
        "auto-detect-interface": True,
        "auto-redirect": False,
        "dns-hijack": list(DNS_HIJACK),
        "iproute2-table-index": TABLE,
        "iproute2-rule-index": RULE_START,
        "route-exclude-address": value["exclude_address"],
    }


def permission():
    try:
        status = Path("/proc/self/status").read_text()
        effective = int(re.search(r"^CapEff:\s*(\w+)", status, re.M)[1], 16)
        capability = bool(effective & (1 << 12))
    except (OSError, TypeError):
        capability = False
    return {
        "root": os.geteuid() == 0,
        "cap_net_admin": capability,
        "tun_access": os.access("/dev/net/tun", os.R_OK | os.W_OK),
    }


def network():
    result = {"links": [], "routes": [], "rules": []}
    try:
        binary = ip_tool()
        commands = [("links", ["-details", "link", "show"])]
        for family in ("-4", "-6"):
            commands.extend(
                [
                    ("routes", [family, "route", "show", "table", "all"]),
                    ("rules", [family, "rule", "show"]),
                ]
            )
        for key, args in commands:
            output = subprocess.run(
                [binary, "-N", "-j", *args],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            )
            values = json.loads(output.stdout)
            if key != "links":
                values = [dict(item, family=args[0]) for item in values]
            result[key].extend(values)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise AppError(
            "tun_inspection_failed",
            "无法读取网络设备或路由。",
            1,
            "安装 iproute2 并允许读取当前网络命名空间。",
            True,
        ) from exc
    return result


def ip_tool():
    """Resolve iproute2 without trusting a capability-bearing process's PATH."""
    for directory in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        path = Path(directory) / "ip"
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise FileNotFoundError("iproute2 executable not found")


def resources(net):
    return {
        "routes": [r for r in net["routes"] if str(r.get("table")) == str(TABLE)],
        "rules": [
            r
            for r in net["rules"]
            if str(r.get("table")) == str(TABLE) or RULE_START <= r.get("priority", -1) <= RULE_END
        ],
    }


def resource_key(item):
    # iproute2 annotates an input/output interface after that interface disappears.
    stable = {
        key: value
        for key, value in item.items()
        if key not in ("iif_detached", "oif_detached")
    }
    return json.dumps(stable, sort_keys=True, separators=(",", ":"))


def recorded_resources(current, expected):
    actual = Counter(resource_key(item) for item in current)
    recorded = Counter(resource_key(item) for item in expected)
    return all(count <= recorded[key] for key, count in actual.items())


def rule_network(rule, key):
    value = rule.get(key)
    if not isinstance(value, str) or value == "all":
        return None
    length = rule.get(key + "len")
    if length is not None and "/" not in value:
        value += "/" + str(length)
    try:
        return ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None


def owned_rule(rule, device="mihomo"):
    """Recognize the fixed policy shape generated by mihomo auto-route."""
    fields = set(rule) - {"iif_detached", "oif_detached"}
    priority = rule.get("priority")
    table = str(rule.get("table"))
    family = rule.get("family")
    if family not in ("-4", "-6"):
        return False
    family_priority = RULE_START + 1 if family == "-4" else RULE_START
    destination = rule_network(rule, "dst")
    source = rule_network(rule, "src")
    tun_network = (
        ipaddress.ip_network("198.18.0.0/30")
        if family == "-4"
        else ipaddress.ip_network("fdfe:dcba:9876::/126")
    )
    if fields == {"priority", "src", "dst", "dstlen", "table", "family"}:
        return (
            priority == RULE_START
            and table == str(TABLE)
            and rule.get("src") == "all"
            and destination is not None
            and destination == tun_network
        )
    if fields == {"priority", "src", "iif", "table", "family"}:
        return (
            priority == RULE_START + 2
            and table == str(TABLE)
            and rule.get("iif") == "lo"
            and rule.get("src") in ("0.0.0.0", "::")
        )
    if fields == {"priority", "not", "src", "iif", "table", "family"}:
        return (
            priority == RULE_START + 2
            and table == str(TABLE)
            and rule.get("src") == "all"
            and rule.get("iif") == "lo"
        )
    if fields == {"priority", "src", "table", "family"}:
        return (
            priority == RULE_START + 2
            and table == str(TABLE)
            and rule.get("src") == "all"
        )
    if fields == {"priority", "src", "srclen", "iif", "table", "family"}:
        return (
            priority == (RULE_START + 2 if family == "-4" else RULE_START + 1)
            and table == str(TABLE)
            and rule.get("iif") == "lo"
            and source == tun_network
        )
    suppress_key = (
        "suppress_prefixlen" if "suppress_prefixlen" in rule else "suppress_prefixlength"
    )
    if fields == {
        "priority", "not", "src", "dport", "table", suppress_key, "family"
    }:
        return (
            priority == family_priority
            and table in ("254", "main")
            and rule.get("src") == "all"
            and str(rule.get("dport")) == "53"
            and rule.get(suppress_key) == 0
        )
    if fields == {"priority", "src", "iif", "goto", "family"}:
        if priority != family_priority or rule.get("goto") != RULE_START + 10:
            return False
        if rule.get("iif") == device and rule.get("src") == "all":
            return True
        halves = (
            (ipaddress.ip_network("0.0.0.0/1"), ipaddress.ip_network("128.0.0.0/1"))
            if family == "-4"
            else (ipaddress.ip_network("::/1"), ipaddress.ip_network("8000::/1"))
        )
        return rule.get("iif") == "lo" and source in halves
    if fields == {"priority", "src", "srclen", "iif", "goto", "family"}:
        halves = (
            (ipaddress.ip_network("0.0.0.0/1"), ipaddress.ip_network("128.0.0.0/1"))
            if family == "-4"
            else (ipaddress.ip_network("::/1"), ipaddress.ip_network("8000::/1"))
        )
        return (
            priority == family_priority
            and rule.get("goto") == RULE_START + 10
            and rule.get("iif") == "lo"
            and source in halves
        )
    return (
        fields == {"priority", "src", "nop", "family"}
        and priority == RULE_START + 10
        and rule.get("src") == "all"
    )


def route_destinations(exclude_address, version):
    routes = [ipaddress.ip_network("0.0.0.0/0" if version == 4 else "::/0")]
    for value in exclude_address:
        excluded = ipaddress.ip_network(value, strict=False)
        if excluded.version != version:
            continue
        remaining = []
        for route in routes:
            if route.subnet_of(excluded):
                continue
            if excluded.subnet_of(route):
                remaining.extend(route.address_exclude(excluded))
            else:
                remaining.append(route)
        routes = remaining
    return set(routes)


def owned_route(route, device, exclude_address=()):
    fields = set(route)
    try:
        destination = (
            ipaddress.ip_network("0.0.0.0/0" if route.get("family") == "-4" else "::/0")
            if route.get("dst") == "default"
            else ipaddress.ip_network(route.get("dst"), strict=False)
        )
    except (TypeError, ValueError):
        return False
    common = (
        str(route.get("table")) == str(TABLE)
        and route.get("dev") == device
        and route.get("flags") == []
    )
    if route.get("family") == "-4":
        return (
            fields == {"family", "table", "dst", "gateway", "dev", "flags"}
            and common
            and destination.version == 4
            and destination in route_destinations(exclude_address, 4)
            and route.get("gateway") == "198.18.0.2"
        )
    return (
        fields
        == {"family", "table", "dst", "gateway", "dev", "metric", "pref", "flags"}
        and common
        and destination.version == 6
        and destination in route_destinations(exclude_address, 6)
        and route.get("family") == "-6"
        and route.get("gateway") == "fdfe:dcba:9876::2"
        and route.get("metric") == 1024
        and route.get("pref") == "medium"
    )


def owned_snapshot(record, net, baseline):
    """Classify reserved resources without claiming foreign additions."""
    current = resources(net)
    if any(baseline.values()):
        raise ValueError("TUN resource baseline is not empty")
    device = record["settings"]["tun"]["device"]
    excluded = record["settings"]["tun"]["exclude_address"]
    owned = {
        "routes": [
            route for route in current["routes"]
            if owned_route(route, device, excluded)
        ],
        "rules": [rule for rule in current["rules"] if owned_rule(rule, device)],
    }
    clean = all(len(owned[key]) == len(current[key]) for key in current)
    return owned, clean


def require_owned_snapshot(record, net, baseline):
    snapshot, clean = owned_snapshot(record, net, baseline)
    if not clean:
        current = resources(net)
        foreign = {
            key: [item for item in current[key] if item not in snapshot[key]]
            for key in current
        }
        raise AppError(
            "tun_route_conflict",
            "TUN 启动期间出现了不属于本实例的路由资源。",
            5,
            "停止占用路由表 2022 或规则优先级 9000–9020 的其他程序后重试。",
        ) from ValueError(f"unrecognized reserved resources: {foreign!r}")
    return snapshot


def rule_delete_command(binary, rule):
    ignored = {"family", "iif_detached", "oif_detached"}
    supported = {
        "priority", "src", "srclen", "dst", "dstlen", "fwmark", "fwmask",
        "iif", "oif", "uidrange",
        "ipproto", "sport", "dport", "tos", "table", "suppress_prefixlength",
        "suppress_prefixlen", "goto", "protocol", "l3mdev", "not", "nop", "action",
    }
    unknown = set(rule) - ignored - supported
    if unknown:
        raise ValueError(f"unrecognized policy rule fields: {sorted(unknown)} in {rule!r}")
    family, priority = rule.get("family"), rule.get("priority")
    if family not in ("-4", "-6") or type(priority) is not int:
        raise ValueError("unrecognized policy rule")
    command = [binary, family, "rule", "del", "priority", str(priority)]
    if "not" in rule:
        command.append("not")
    for key, option in (("src", "from"), ("dst", "to")):
        if key in rule:
            value = str(rule[key])
            length = rule.get(key + "len")
            if length is not None and value != "all" and "/" not in value:
                value += "/" + str(length)
            command.extend((option, value))
    if "fwmark" in rule:
        mark = str(rule["fwmark"])
        if "fwmask" in rule:
            mark += "/" + str(rule["fwmask"])
        command.extend(("fwmark", mark))
    for key in ("iif", "oif", "uidrange", "ipproto", "sport", "dport", "tos"):
        if key in rule:
            command.extend((key, str(rule[key])))
    if rule.get("l3mdev"):
        command.append("l3mdev")
    if "table" in rule:
        command.extend(("table", str(rule["table"])))
    suppress = rule.get("suppress_prefixlen", rule.get("suppress_prefixlength"))
    if suppress is not None:
        command.extend(("suppress_prefixlength", str(suppress)))
    if "protocol" in rule:
        command.extend(("protocol", str(rule["protocol"])))
    if "goto" in rule:
        command.extend(("goto", str(rule["goto"])))
    elif "nop" in rule:
        command.append("nop")
    elif rule.get("action") in ("blackhole", "unreachable", "prohibit"):
        command.append(rule["action"])
    elif "action" in rule:
        raise ValueError("unrecognized policy rule action")
    return command


def route_delete_command(binary, route):
    supported = {
        "family", "table", "dst", "gateway", "dev", "protocol", "scope",
        "prefsrc", "metric", "type", "pref", "flags",
    }
    unknown = set(route) - supported
    if unknown or route.get("flags") not in (None, []):
        raise ValueError(f"unrecognized route fields: {sorted(unknown)} in {route!r}")
    family = route.get("family")
    if family not in ("-4", "-6") or str(route.get("table")) != str(TABLE):
        raise ValueError("unrecognized route")
    route_type = route.get("type", "unicast")
    if route_type not in ("unicast", "blackhole", "unreachable", "prohibit", "throw"):
        raise ValueError("unrecognized route type")
    command = [binary, family, "route", "del"]
    if route_type != "unicast":
        command.append(route_type)
    command.extend((str(route.get("dst", "default")), "table", str(TABLE)))
    for key, option in (
        ("gateway", "via"), ("dev", "dev"), ("protocol", "proto"),
        ("scope", "scope"), ("prefsrc", "src"), ("metric", "metric"),
        ("pref", "pref"),
    ):
        if key in route:
            command.extend((option, str(route[key])))
    return command


def valid_resource(kind, item):
    if not isinstance(item, dict):
        return False
    try:
        if kind == "routes":
            if not (
                isinstance(item.get("dst"), str)
                and isinstance(item.get("dev"), str)
                and type(item.get("table")) in (str, int)
            ):
                return False
            route_delete_command("ip", item)
        elif kind == "rules":
            if not (
                item.get("family") in ("-4", "-6")
                and type(item.get("priority")) is int
                and (
                    "table" not in item
                    or type(item["table"]) in (str, int)
                )
            ):
                return False
            rule_delete_command("ip", item)
        else:
            return False
    except (TypeError, ValueError):
        return False
    return True


def valid_snapshot(snapshot, settings):
    try:
        if not (
            isinstance(snapshot, dict)
            and set(snapshot) == {"routes", "rules"}
            and all(isinstance(snapshot[key], list) for key in snapshot)
        ):
            return False
        device = settings["tun"]["device"]
        excluded = settings["tun"]["exclude_address"]
        return all(
            valid_resource("routes", route) and owned_route(route, device, excluded)
            for route in snapshot["routes"]
        ) and all(
            valid_resource("rules", rule) and owned_rule(rule, device)
            for rule in snapshot["rules"]
        )
    except (KeyError, TypeError, ValueError):
        return False


def owns_device(record):
    if not record or not record["settings"].get("tun", DEFAULT_TUN)["enabled"]:
        return False
    device = record["settings"]["tun"]["device"]
    try:
        # Linux exposes the actual attached TUN interface on the open descriptor.
        for path in Path(f"/proc/{record['pid']}/fdinfo").iterdir():
            try:
                info = path.read_text()
            except FileNotFoundError:
                # Descriptors can close while we inspect the live process.
                continue
            if re.search(r"^iff:\s*" + re.escape(device) + r"\s*$", info, re.M):
                return True
    except PermissionError as exc:
        raise AppError(
            "tun_inspection_denied", "无权限确认内核持有的 TUN 设备。", 4,
            "使用 mihomo-py --sudo tun status 检查实际状态。",
        ) from exc
    except OSError:
        pass
    return False


def preflight(value, record=None):
    validate(value)
    if sys.platform != "linux":
        raise AppError(
            "tun_unsupported_platform",
            "TUN 仅支持 Linux。",
            2,
            "在支持 /dev/net/tun 的 Linux 上运行。",
        )
    perms = permission()
    # UID 0 in a restricted container does not imply CAP_NET_ADMIN.
    if not perms["cap_net_admin"] or not perms["tun_access"]:
        raise AppError(
            "tun_permission_denied",
            "缺少 TUN 访问权限或 CAP_NET_ADMIN。",
            4,
            "使用 mihomo-py --sudo 启动，或由管理员授予 CAP_NET_ADMIN 和 TUN 设备访问权限。",
        )
    net = network()
    owned = owns_device(record)
    existing = next((x for x in net["links"] if x["ifname"] == value["device"]), None)
    if existing and not (owned and record["settings"]["tun"]["device"] == value["device"]):
        raise AppError(
            "tun_device_conflict",
            "TUN 设备已被占用。",
            5,
            "使用 --device 指定空闲设备名，或停止所属实例。",
        )
    current = resources(net)
    previous = record.get("tun_resources", {}) if owned else {}
    if any(
        not recorded_resources(current[key], previous.get(key, []))
        for key in current
    ):
        raise AppError(
            "tun_route_conflict",
            "TUN 路由表或策略路由规则已被占用。",
            5,
            "停止使用路由表 2022 或规则优先级 9000–9020 的实例后重试。",
        )
    return net


def observation(record, net=None):
    if not record or not record["settings"].get("tun", DEFAULT_TUN)["enabled"]:
        return {"active": False, "ipv4": False, "ipv6": False}
    net = network() if net is None else net
    device = record["settings"]["tun"]["device"]
    link = next((x for x in net["links"] if x["ifname"] == device), None)
    owned = owns_device(record)
    expected = record.get("tun_resources", {})
    result = {"active": False}
    for family, label in (("-4", "ipv4"), ("-6", "ipv6")):
        routes = any(
            r.get("dev") == device and r["family"] == family and str(r.get("table")) == str(TABLE)
            for r in net["routes"]
        )
        rules = [r for r in net["rules"] if r["family"] == family]
        # The subnet-only rule at 9000 is insufficient for local traffic. Wait
        # for the catch-all policy and its terminal rule during initial startup.
        policy = any(
            str(r.get("table")) == str(TABLE) and r.get("priority") == RULE_START + 2
            for r in rules
        ) and any(r.get("priority") == RULE_START + 10 and "nop" in r for r in rules)
        intact = all(
            recorded_resources(
                [item for item in expected.get(key, []) if item["family"] == family],
                [item for item in net[key] if item.get("family") == family],
            )
            for key in ("routes", "rules")
        )
        result[label] = bool(link and "UP" in link.get("flags", []) and owned
                             and routes and policy and intact)
    expected_ipv6 = any(r["family"] == "-6" for r in expected.get("routes", []))
    result["active"] = result["ipv4"] and (not expected_ipv6 or result["ipv6"])
    return result


def settled_network(record):
    """Wait briefly for the kernel to unregister a TUN link after process exit."""
    net = network()
    device = record["settings"]["tun"]["device"]
    deadline = time.monotonic() + 1
    while any(x["ifname"] == device for x in net["links"]) and time.monotonic() < deadline:
        time.sleep(0.05)
        net = network()
    return net


def check_cleanup(record):
    if not record or not record["settings"].get("tun", DEFAULT_TUN)["enabled"]:
        return
    try:
        net = settled_network(record)
        device = record["settings"]["tun"]["device"]
        remaining = resources(net)
        if any(x["ifname"] == device for x in net["links"]) or any(remaining.values()):
            raise ValueError
    except (AppError, ValueError) as exc:
        raise AppError(
            "tun_cleanup_failed",
            "无法确认 TUN 设备和路由已清理。",
            1,
            "运行 tun doctor 并由管理员检查残留；不要删除其他实例的设备或路由。",
            True,
        ) from exc


def cleanup(record):
    """Verify teardown and remove only residual resources recorded for this instance."""
    if not record or not record["settings"].get("tun", DEFAULT_TUN)["enabled"]:
        return
    try:
        net = settled_network(record)
        device = record["settings"]["tun"]["device"]
        remaining = resources(net)
        if not any(x["ifname"] == device for x in net["links"]) and not any(
            remaining.values()
        ):
            return

        expected = record.get("tun_resources")
        recoverable = (
            not any(x["ifname"] == device for x in net["links"])
            and valid_snapshot(expected, record["settings"])
            and all(
                recorded_resources(remaining[key], expected[key])
                for key in remaining
            )
        )
        if not recoverable:
            raise ValueError("residual resources do not match the recorded snapshot")
        if not permission()["cap_net_admin"]:
            raise PermissionError("CAP_NET_ADMIN is required for residual cleanup")

        binary = ip_tool()
        operation_error = None
        for kind, items, command_builder in (
            ("rules", remaining["rules"], rule_delete_command),
            ("routes", remaining["routes"], route_delete_command),
        ):
            for item in items:
                # Another privileged process may modify the reserved range between
                # operations. Stop before touching anything not in our snapshot.
                current = resources(network())
                if any(
                    not recorded_resources(current[key], expected[key])
                    for key in current
                ):
                    raise ValueError("new residual resources appeared during cleanup")
                if not any(resource_key(value) == resource_key(item) for value in current[kind]):
                    continue
                command = command_builder(binary, item)
                try:
                    subprocess.run(
                        command,
                        capture_output=True,
                        timeout=3,
                        check=True,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    # Kernel/core teardown can win this race; final inspection decides success.
                    operation_error = operation_error or exc
        try:
            check_cleanup(record)
        except AppError as exc:
            raise operation_error or exc
    except PermissionError as exc:
        raise AppError(
            "tun_cleanup_permission_denied",
            "异常退出后需要 CAP_NET_ADMIN 清理本实例残留的 TUN 路由。",
            4,
            "使用 mihomo-py --sudo core stop 清理后重试。",
        ) from exc
    except (AppError, OSError, subprocess.SubprocessError, ValueError) as exc:
        if isinstance(exc, AppError) and exc.kind == "tun_cleanup_permission_denied":
            raise
        raise AppError(
            "tun_cleanup_failed",
            "无法确认 TUN 设备和路由已清理。",
            1,
            "运行 tun doctor 并由管理员检查残留；不要删除其他实例的设备或路由。",
            True,
        ) from exc


def serialized(method):
    """Serialize network transitions across data directories in this network namespace."""

    @functools.wraps(method)
    def wrapper(engine, *args, **kwargs):
        if getattr(engine, "_network_guard", False):
            return method(engine, *args, **kwargs)
        proposed = kwargs.get("settings", args[2] if len(args) > 2 else {})
        record = engine.record()
        current = record["settings"] if record else {}
        if not any(s.get("tun", DEFAULT_TUN)["enabled"] for s in (proposed, current)):
            return method(engine, *args, **kwargs)
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as guard:
            try:
                guard.bind("\0mihomo-py-network-transition")
            except OSError as exc:
                raise AppError(
                    "busy", "另一个实例正在修改内核或网络。", 5, "稍后重试。", True
                ) from exc
            engine._network_guard = True
            try:
                return method(engine, *args, **kwargs)
            finally:
                engine._network_guard = False

    return wrapper
