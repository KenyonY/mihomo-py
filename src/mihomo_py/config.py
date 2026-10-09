import copy
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from .download import SubscriptionHTTPSHandler
from .errors import AppError

MAX_BYTES = 8 * 1024 * 1024


def check_name(name):
    if not re.fullmatch(r"[\w][\w.-]{0,63}", name):
        raise AppError("invalid_name", "名称限 1–64 个字母、数字、中文、下划线、点或连字符。", 2)


def normalize_source(source):
    if "://" in source:
        try:
            url = urllib.parse.urlsplit(source)
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ValueError
            _ = url.port
        except ValueError as exc:
            raise AppError(
                "invalid_source", "订阅地址必须是无内嵌用户名和密码的 HTTP(S) URL。", 2
            ) from exc
        return url.geturl()
    return str(Path(source).expanduser().resolve())


def source_label(source):
    if source.startswith(("http://", "https://")):
        url = urllib.parse.urlsplit(source)
        return f"{url.scheme}://{url.hostname}/…"
    return source


def fetch(source, *, progress=None):
    try:
        return _fetch_once(source)
    except AppError as exc:
        if exc.kind != "download_timeout" or not source.startswith(("http://", "https://")):
            raise
        if progress:
            progress("订阅连接或读取超时，正在重试一次…")
        return _fetch_once(source)


def _fetch_once(source):
    try:
        if source.startswith(("http://", "https://")):
            # Subscriptions are fetched directly, even when the shell exports proxy variables.
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}), SubscriptionHTTPSHandler()
            )
            request = urllib.request.Request(source, headers={"User-Agent": "Clash.Meta/mihomo-py"})
            with opener.open(request, timeout=20) as response:
                data = response.read(MAX_BYTES + 1)
        else:
            with Path(source).open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise AppError(
            "download_failed",
            f"订阅下载失败（HTTP {exc.code}）。",
            suggestion="检查订阅地址是否过期；使用 sub set 修改地址。",
            retryable=exc.code >= 500 or exc.code == 429,
        ) from exc
    except TimeoutError as exc:
        raise AppError(
            "download_timeout", "读取订阅来源超时，尚未取得完整配置。", retryable=True
        ) from exc
    except PermissionError:
        raise
    except (OSError, urllib.error.URLError, ValueError) as exc:
        if isinstance(getattr(exc, "reason", None), TimeoutError):
            raise AppError(
                "download_timeout", "连接订阅来源超时，尚未取得配置。", retryable=True
            ) from exc
        raise AppError(
            "source_unavailable", "无法读取订阅来源。请检查文件或网络。", retryable=True
        ) from exc
    if not data or len(data) > MAX_BYTES:
        raise AppError("invalid_config", "订阅为空或超过 8 MiB。", 2)
    try:
        content = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AppError("invalid_config", "配置必须是 UTF-8 YAML。", 2) from exc
    parse(content)
    return content


def parse(content):
    try:
        value = yaml.safe_load(content)
    except (yaml.YAMLError, RecursionError) as exc:
        raise AppError("invalid_config", "无法解析 YAML 配置。", 2) from exc
    if not isinstance(value, dict) or not any(
        key in value for key in ("proxies", "proxy-providers", "proxy-groups", "rules")
    ):
        raise AppError("invalid_config", "需要 Clash/Mihomo YAML 配置，不支持节点 URI 列表。", 2)
    return value


def render(content, settings, secret, *, dashboard=None):
    config = copy.deepcopy(parse(content))
    # Local settings own both listeners and the bundled UI. Subscriptions cannot expose them.
    for key in (
        "external-doh-server",
        "iptables",
        "ntp",
        "ss-config",
        "vmess-config",
        "tuic-server",
        "listeners",
        "tunnels",
        "external-controller-tls",
        "external-controller-unix",
        "external-controller-pipe",
        "external-ui",
        "external-ui-url",
        "external-ui-name",
    ):
        config.pop(key, None)
    config.update(
        {
            "mixed-port": settings["proxy_port"],
            "port": 0,
            "socks-port": 0,
            "redir-port": 0,
            "tproxy-port": 0,
            "allow-lan": settings["host"] != "127.0.0.1",
            "bind-address": settings["host"],
            "external-controller": f"{settings['controller_host']}:{settings['controller_port']}",
            "secret": secret,
            "mode": settings["mode"],
            "tun": {"enable": False},
            "authentication": [],
            # The bundled snapshot must not trigger overseas downloads after bootstrap.
            "geo-auto-update": False,
        }
    )
    if dashboard is not None:
        config["external-ui"] = dashboard
    dns = config.get("dns")
    if isinstance(dns, dict):
        dns.pop("listen", None)
    profile = config.setdefault("profile", {})
    if not isinstance(profile, dict):
        raise AppError("invalid_config", "profile 必须是 YAML 对象。", 2)
    profile["store-selected"] = True
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
