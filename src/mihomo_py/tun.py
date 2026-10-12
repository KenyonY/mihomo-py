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
            and isinstance(expected, dict)
            and set(expected) == {"routes", "rules"}
            and all(isinstance(expected[key], list) for key in expected)
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
        for rule in remaining["rules"]:
            family, priority = rule.get("family"), rule.get("priority")
            if family not in ("-4", "-6") or type(priority) is not int:
                raise ValueError("unrecognized policy rule")
            try:
                subprocess.run(
                    [binary, family, "rule", "del", "priority", str(priority)],
                    capture_output=True,
                    timeout=3,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                # Kernel/core teardown can win this race; final inspection decides success.
                operation_error = operation_error or exc
        for family in ("-4", "-6"):
            if any(route.get("family") == family for route in remaining["routes"]):
                try:
                    subprocess.run(
                        [binary, family, "route", "flush", "table", str(TABLE)],
                        capture_output=True,
                        timeout=3,
                        check=True,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
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
