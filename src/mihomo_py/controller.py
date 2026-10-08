"""Authenticated requests to the owned core, shared by CLI and TUI."""

import json
import urllib.error
import urllib.parse
import urllib.request

from .config import normalize_source
from .errors import AppError

DEFAULT_TEST_URL = "https://www.gstatic.com/generate_204"


def instance_id(record):
    if record is None:
        return None
    identity = record["identity"]
    return f"{record['pid']}:{identity['boot_id']}:{identity['start_ticks']}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Controller:
    def __init__(self, engine, expected=None):
        self.engine = engine
        self.record = engine.running()
        if not self.record:
            raise AppError("core_stopped", "内核未运行。", 3, "先启动内核，再查看或切换节点。")
        self.instance = instance_id(self.record)
        if expected is not None and self.instance != expected:
            self.stale()

    @staticmethod
    def stale():
        raise AppError("stale_instance", "内核实例已变化，请刷新后重新操作。", 5)

    def request(self, path, *, method="GET", body=None, timeout=3):
        if instance_id(self.engine.running()) != self.instance:
            self.stale()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.record['settings']['controller_port']}{path}",
            data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self.record['secret']}",
                "Content-Type": "application/json",
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=timeout) as response:
                result = None if response.status == 204 else json.load(response)
        except urllib.error.HTTPError as exc:
            code = 4 if exc.code in (401, 403) else 3 if exc.code == 404 else 1
            raise AppError(
                "api_error",
                f"内核 API 请求失败（HTTP {exc.code}）。",
                code,
                "刷新后重试；测速失败可更换测试地址或节点。",
                retryable=exc.code >= 500,
            ) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise AppError(
                "api_unavailable", "无法连接内核 API 或请求超时。", retryable=True
            ) from exc
        except (ValueError, UnicodeError) as exc:
            raise AppError("invalid_response", "内核 API 返回了无效 JSON。") from exc
        if instance_id(self.engine.running()) != self.instance:
            self.stale()
        return result

    def proxies(self):
        result = self.request("/proxies")
        if not isinstance(result, dict) or not isinstance(result.get("proxies"), dict):
            raise AppError("invalid_response", "内核 API 缺少 proxies 数据。")
        proxies = result["proxies"]
        for name, item in proxies.items():
            if not isinstance(item, dict) or not isinstance(item.get("type"), str):
                raise AppError("invalid_response", "内核节点数据无效。")
            if "all" in item and not (
                isinstance(item["all"], list) and all(isinstance(n, str) for n in item["all"])
            ):
                raise AppError("invalid_response", "内核代理组数据无效。")
        return proxies

    @staticmethod
    def groups(proxies):
        return [
            {
                "name": name,
                "type": item["type"],
                "selected": item.get("now"),
                "count": len(item["all"]),
                "selectable": item["type"] == "Selector",
            }
            for name, item in proxies.items()
            if "all" in item
        ]

    @staticmethod
    def members(proxies, group):
        if group not in proxies or "all" not in proxies[group]:
            raise AppError("not_found", "未找到此代理组。", 3)
        result = []
        for name in dict.fromkeys(proxies[group]["all"]):
            item = proxies.get(name, {})
            history = item.get("history") or []
            if not isinstance(history, list):
                history = []
            delay = history[-1].get("delay") if history and isinstance(history[-1], dict) else None
            result.append(
                {
                    "name": name,
                    "type": item.get("type", "Unknown"),
                    "selected": name == proxies[group].get("now"),
                    "delay_ms": delay if isinstance(delay, (int, float)) and delay > 0 else None,
                }
            )
        return result

    def select(self, group, name):
        proxies = self.proxies()
        members = self.members(proxies, group)
        if proxies[group]["type"] != "Selector":
            raise AppError("not_selectable", "当前仅支持手动选择组（Selector）的节点切换。", 2)
        if name not in [item["name"] for item in members]:
            raise AppError("not_found", "节点不属于此代理组，请刷新列表。", 3)
        self.request(
            f"/proxies/{urllib.parse.quote(group, safe='')}", method="PUT", body={"name": name}
        )
        selected = self.proxies()[group].get("now")
        if selected != name:
            raise AppError("selection_failed", "内核未确认节点切换，请刷新检查。")
        return {"group": group, "selected": selected}

    def test(self, name, url=DEFAULT_TEST_URL, timeout_ms=5000):
        url = normalize_source(url)
        if not url.startswith(("http://", "https://")):
            raise AppError("invalid_url", "测速地址必须是 HTTP(S) URL。", 2)
        if not 1 <= timeout_ms <= 30000:
            raise AppError("invalid_timeout", "测速超时须在 1–30000 毫秒内。", 2)
        if name not in self.proxies():
            raise AppError("not_found", "未找到此节点。", 3)
        query = urllib.parse.urlencode({"url": url, "timeout": timeout_ms})
        result = self.request(
            f"/proxies/{urllib.parse.quote(name, safe='')}/delay?{query}",
            timeout=timeout_ms / 1000 + 2,
        )
        delay = result.get("delay") if isinstance(result, dict) else None
        if type(delay) is not int or delay < 0:
            raise AppError("invalid_response", "内核未返回有效的延迟结果。")
        return {"name": name, "delay_ms": delay}
