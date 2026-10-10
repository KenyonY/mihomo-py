import copy
import fcntl
import json
import os
import select
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timezone

import yaml

from . import pidfd
from .bundle import dashboard_root
from .config import check_name, fetch, normalize_source, parse, source_label
from .engine import Engine, fingerprint, process_identity
from .errors import AppError, Message
from .store import Store, atomic_write, controller_address, valid_host


class Manager:
    def __init__(self, directory, binary=None, timeout=20):
        self.store = Store(directory)
        self.engine = Engine(self.store.root, binary, timeout)

    def subscription(self, state, name=None):
        name = name or state["selected"]
        if not name or name not in state["subs"]:
            raise AppError(
                "not_found",
                "未找到订阅或尚未选择订阅。",
                3,
                "用 sub list 查看，再用 sub use 选择。",
            )
        return name, state["subs"][name]

    def list_subs(self):
        state = self.store.read()
        return [self.describe(state, name) for name in state["subs"]]

    def describe(self, state, name):
        sub = state["subs"][name]
        return {
            "name": name,
            "selected": name == state["selected"],
            "source": source_label(sub["source"]),
            "updated_at": sub["updated_at"],
        }

    def status(self):
        state = self.store.read()
        running = self.engine.running()
        return {
            "running": bool(running),
            "pid": running["pid"] if running else None,
            "selected": state["selected"],
            "running_subscription": running["subscription"] if running else None,
            "settings": state["settings"],
            "running_settings": running["settings"] if running else None,
            "healthy": self.engine.healthy(running) if running else False,
            "data_dir": str(self.store.root),
        }

    def web_gateway(self):
        path = self.store.root / "web-service.json"
        try:
            record = json.loads(path.read_text())
        except FileNotFoundError:
            return None
        identity = process_identity(record["pid"])
        if identity and identity == record["identity"]:
            return record
        return None

    @contextmanager
    def web_control(self):
        # The child needs the state lock to register; use a separate lock for
        # parent lifecycle operations and release it before returning to the UI.
        self.store.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.store.root / ".web-control.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AppError("busy", "Web 服务正在开启或关闭，请稍后重试。", 5) from exc
            yield
        finally:
            os.close(fd)

    def start_web(self, *, host=None, port=9091):
        with self.web_control():
            return self._start_web(host=host, port=port)

    def _start_web(self, *, host=None, port=9091):
        """Start the subscription gateway as a detached child process."""
        if dashboard_root() is None:
            raise AppError(
                "web_missing", "尚未安装 Web 资源。", 3, "pip install 'mihomo-py[web]'。"
            )
        settings = self.store.read()["settings"]
        host = host or settings["controller_host"]
        if port in (settings["controller_port"], settings["proxy_port"]):
            raise AppError("invalid_ports", "Web 服务端口须与管理 API、代理端口不同。", 2)
        if self.web_gateway():
            return self.web()
        self.store.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        command = [
            sys.executable,
            "-m",
            "mihomo_py",
            "--data-dir",
            str(self.store.root),
            "--timeout",
            str(self.engine.timeout),
            "web",
            "serve",
            "--host",
            host,
            "--port",
            str(port),
        ]
        if self.engine.binary:
            command[5:5] = ["--core-binary", self.engine.binary]
        log_path = self.store.root / "web.log"
        log_path.touch(mode=0o600, exist_ok=True)
        log_path.chmod(0o600)
        log = log_path.open("ab")
        try:
            child = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )
        finally:
            log.close()
        deadline = time.monotonic() + 5
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            info = self.web_gateway()
            if info:
                address = controller_address({"controller_host": info["host"]})
                request = urllib.request.Request(
                    f"http://{address}:{info['port']}/mihomo-py/api/subscriptions",
                    headers={"Authorization": "Bearer " + self.engine.controller_secret()},
                )
                try:
                    with opener.open(request, timeout=0.5) as response:
                        if response.status == 200:
                            return self.web()
                except (OSError, urllib.error.URLError):
                    pass
            if child.poll() is not None:
                break
            time.sleep(0.05)
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        raise AppError(
            "web_start_failed",
            "Web 服务未能启动，请检查 web.log。",
            5,
            "运行 mihomo-py web serve 可查看前台错误。",
        )

    def stop_web(self):
        with self.web_control():
            return self._stop_web()

    def _stop_web(self):
        """Stop only the detached subscription gateway owned by this instance."""
        path = self.store.root / "web-service.json"
        try:
            record = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return False
        identity = process_identity(record.get("pid"))
        expected = record.get("identity")
        if not identity or identity != expected:
            path.unlink(missing_ok=True)
            return False
        try:
            fd = pidfd.open_pidfd(record["pid"])
        except ProcessLookupError:
            path.unlink(missing_ok=True)
            return False
        try:
            if process_identity(record["pid"]) != expected:
                return False
            pidfd.send_signal(fd, signal.SIGTERM)
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            if not poller.poll(5000):
                pidfd.send_signal(fd, signal.SIGKILL)
                if not poller.poll(3000):
                    raise AppError("web_stop_failed", "Web 服务尚未退出，请检查进程状态。", 5)
        except ProcessLookupError:
            pass
        finally:
            os.close(fd)
        try:
            if json.loads(path.read_text()) == record:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        try:
            os.waitpid(record["pid"], os.WNOHANG)
        except ChildProcessError:
            pass
        return True

    def web(self):
        gateway = self.web_gateway()
        if gateway:
            return {
                "url": f"http://{controller_address({'controller_host': gateway['host']})}:"
                       f"{gateway['port']}/",
                "listen_host": gateway["host"], "port": gateway["port"],
                "secret": self.engine.controller_secret(),
                "hint": "此入口提供订阅管理与节点面板；远程访问时换成服务器 IP。",
            }
        record = self.engine.running()
        if not record:
            raise AppError("core_stopped", "内核未运行。", 3, "先运行 core start。")
        if dashboard_root() is None:
            raise AppError(
                "web_missing", "尚未安装 Web 面板资源。", 3,
                "运行 pip install 'mihomo-py[web]'，然后 core restart。",
            )
        if not parse(self.engine.runtime_path.read_text()).get("external-ui"):
            raise AppError(
                "web_restart_required", "当前内核尚未加载 Web 面板。", 5,
                "安装 [web] 后运行 core restart，再打开 Web 面板。",
            )
        settings = record["settings"]
        return {
            "url": f"http://{controller_address(settings)}:{settings['controller_port']}/ui/",
            "listen_host": settings["controller_host"],
            "port": settings["controller_port"],
            "secret": record["secret"],
            "hint": "从其他设备访问时，将 URL 中的地址换成服务器 IP；登录时填写此密钥。",
        }

    def commit(self, new):
        running = self.engine.running()
        replaced = False
        previous = self.engine.runtime_path.read_text() if running else None
        if running and new["selected"]:
            name, sub = self.subscription(new)
            digest = fingerprint(sub["content"], new["settings"])
            if running["subscription"] != name or running["fingerprint"] != digest:
                self.engine.replace(sub["content"], new["settings"], name)
                replaced = True
        try:
            self.store.save(new)
        except OSError:
            if replaced:
                self.engine.stop()
                self.engine.launch(
                    previous,
                    running["subscription"],
                    running["settings"],
                    running["fingerprint"],
                    running["secret"],
                    data_directory=running["data_dir"],
                )
            raise

    def set_secret(self, secret):
        """Call under the instance lock; commit the credential only after core readiness."""
        if not (
            isinstance(secret, str) and 1 <= len(secret) <= 256
            and all("!" <= character <= "~" for character in secret)
        ):
            raise AppError("invalid_secret", "密钥须为 1–256 个可见 ASCII 字符，不含空格。", 2)
        if secret == self.engine.controller_secret():
            return {"changed": False}
        old = self.engine.running()
        previous = self.engine.runtime_path.read_text() if old else None
        if old:
            config = parse(previous)
            config["secret"] = secret
            self.engine.replace_runtime(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                old["subscription"], old["settings"], old["fingerprint"], secret,
            )
        try:
            atomic_write(self.store.root / "controller-secret", secret + "\n")
        except OSError:
            if old:
                try:
                    self.engine.replace_runtime(
                        previous, old["subscription"], old["settings"],
                        old["fingerprint"], old["secret"],
                    )
                except Exception as failure:
                    raise AppError(
                        "rollback_failed", "密钥保存失败，恢复旧实例也失败。",
                        suggestion="检查 core logs 后重新 core start。",
                    ) from failure
            raise
        return {"changed": True}

    def put_sub(self, name, source=None, *, create=False, progress=None):
        check_name(name)
        old = self.store.read()
        if create and name in old["subs"]:
            raise AppError(
                "already_exists",
                Message("订阅 {name} 已存在。", name=name),
                5,
                "用 sub set 修改来源。",
            )
        if not create:
            _, existing = self.subscription(old, name)
            source = source or existing["source"]
        source = normalize_source(source)
        if progress:
            progress("正在读取订阅来源…")
        content = fetch(source, progress=progress)
        self.engine.validate(content, old["settings"], name, progress=progress)
        new = copy.deepcopy(old)
        new["subs"][name] = {
            "source": source,
            "content": content,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if progress:
            progress("配置校验通过，正在应用并保存订阅…")
        self.commit(new)
        return self.describe(new, name)

    def use(self, name):
        old = self.store.read()
        self.subscription(old, name)
        new = copy.deepcopy(old)
        new["selected"] = name
        self.commit(new)
        return self.describe(new, name)

    def remove(self, name):
        old = self.store.read()
        self.subscription(old, name)
        running = self.engine.running()
        if running and (name == old["selected"] or name == running["subscription"]):
            raise AppError(
                "in_use", "无法删除正在使用的订阅。", 5, "先 core stop 或 sub use 其他订阅。"
            )
        new = copy.deepcopy(old)
        del new["subs"][name]
        if new["selected"] == name:
            new["selected"] = None
        self.commit(new)
        return {"removed": name}

    def configure(self, values):
        old = self.store.read()
        new = copy.deepcopy(old)
        new["settings"].update(values)
        for key in ("host", "controller_host"):
            if not valid_host(new["settings"][key]):
                raise AppError("invalid_host", "监听地址须为 IPv4 地址，例如 0.0.0.0。", 2)
        if new["settings"]["proxy_port"] == new["settings"]["controller_port"]:
            raise AppError("invalid_ports", "代理端口和管理端口必须不同。", 2)
        self.commit(new)
        return new["settings"]

    def start(self, restart=False):
        state = self.store.read()
        name, sub = self.subscription(state)
        running = self.engine.running()
        if not restart and running:
            if (
                running["subscription"] == name
                and running["fingerprint"]
                == fingerprint(
                    sub["content"],
                    state["settings"],
                )
                and self.engine.healthy(running)
            ):
                return self.status()
        self.engine.replace(sub["content"], state["settings"], name)
        return self.status()
