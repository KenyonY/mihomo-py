import hashlib
import json
import os
import secrets
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import render
from .errors import AppError
from .store import atomic_write, valid_settings


def fingerprint(content, settings):
    return hashlib.sha256((content + json.dumps(settings, sort_keys=True)).encode()).hexdigest()


def process_identity(pid):
    try:
        # The parenthesized process name may itself contain spaces and ')'.
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        if stat[0] == "Z":
            return None
        return {
            "start_ticks": stat[19],
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "cmdline": Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\0")[:-1],
        }
    except (OSError, UnicodeError, IndexError):
        return None


class Engine:
    def __init__(self, root, binary="mihomo", timeout=20):
        self.root = root
        self.binary = binary
        self.timeout = timeout
        self.record_path = root / "process.json"
        self.runtime_path = root / "runtime.yaml"
        self.log_path = root / "core.log"

    def executable(self):
        self.require_process_api()
        binary = shutil.which(self.binary)
        if not binary:
            raise AppError(
                "core_missing",
                "找不到 mihomo 内核。",
                3,
                "先安装 mihomo，或用 --core-binary /path/to/mihomo 指定路径。",
            )
        return str(Path(binary).resolve())

    @staticmethod
    def require_process_api():
        if sys.platform != "linux" or not all(
            (hasattr(os, "pidfd_open"), hasattr(signal, "pidfd_send_signal"))
        ):
            raise AppError(
                "unsupported_python",
                "当前 Python 未提供 Linux pidfd 进程控制接口。",
                suggestion="使用系统 Python 3.11+ 创建虚拟环境后安装；部分 Conda 构建不支持。",
            )
        try:
            descriptor = os.pidfd_open(os.getpid())
            os.close(descriptor)
        except OSError as exc:
            raise AppError(
                "unsupported_platform", "当前 Linux 环境不支持 pidfd 进程控制。"
            ) from exc

    def data_dir(self, name):
        # Providers and selector caches from different subscriptions must not collide.
        key = hashlib.sha256(name.encode()).hexdigest()[:16]
        directory = self.root / "core-data" / key
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory

    def validate(self, content, settings, name):
        binary = self.executable()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".check-", suffix=".yaml", dir=self.root)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(render(content, settings, "validation-only"))
            try:
                result = subprocess.run(
                    [binary, "-t", "-d", str(self.data_dir(name)), "-f", temporary],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired as exc:
                raise AppError(
                    "validation_timeout", "内核校验超时，原配置未替换。", retryable=True
                ) from exc
            if result.returncode:
                # Keep raw diagnostics private: the core can echo subscription credentials.
                atomic_write(self.root / "validation.log", result.stdout.decode(errors="replace"))
                raise AppError(
                    "validation_failed",
                    "mihomo 拒绝此配置，原配置未替换。",
                    2,
                    f"查看本地诊断文件：{self.root / 'validation.log'}",
                )
        finally:
            Path(temporary).unlink(missing_ok=True)

    def record(self):
        if not self.record_path.exists():
            return None
        try:
            record = json.loads(self.record_path.read_text())
            if type(record["pid"]) is not int or record["pid"] <= 1:
                raise ValueError
            identity = record["identity"]
            if not (
                isinstance(identity, dict)
                and all(isinstance(identity[key], str) for key in ("start_ticks", "boot_id"))
                and isinstance(identity["cmdline"], list)
                and all(isinstance(value, str) for value in identity["cmdline"])
                and all(
                    isinstance(record[key], str)
                    for key in ("data_dir", "subscription", "fingerprint", "secret")
                )
                and valid_settings(record["settings"])
            ):
                raise ValueError
            return record
        except (ValueError, KeyError, TypeError) as exc:
            raise AppError(
                "invalid_process_state", "process.json 损坏，无法确认进程身份。"
            ) from exc

    def running(self):
        record = self.record()
        if record and process_identity(record["pid"]) == record["identity"]:
            # Do not trust a PID alone, even if it now points at another mihomo instance.
            expected = ["-d", record["data_dir"], "-f", str(self.runtime_path)]
            if record["identity"]["cmdline"][1:] == expected:
                return record
        return None

    def stop(self):
        record = self.running()
        if not record:
            return False
        self.require_process_api()
        try:
            fd = os.pidfd_open(record["pid"])
        except ProcessLookupError:
            return False
        try:
            if process_identity(record["pid"]) != record["identity"]:
                return False
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            if not poller.poll(5000):
                signal.pidfd_send_signal(fd, signal.SIGKILL)
                if not poller.poll(3000):
                    raise AppError("stop_failed", "内核尚未退出，请检查进程状态。")
        except ProcessLookupError:
            pass
        finally:
            os.close(fd)
        self.record_path.unlink(missing_ok=True)
        return True

    @staticmethod
    def listeners(settings):
        return {
            (socket.SOCK_STREAM, settings["proxy_port"]),
            (socket.SOCK_DGRAM, settings["proxy_port"]),
            (socket.SOCK_STREAM, settings["controller_port"]),
        }

    def check_ports(self, settings, previous=None):
        owned = self.listeners(previous) if previous else set()
        for kind, port in self.listeners(settings) - owned:
            with socket.socket(type=kind) as sock:
                if kind == socket.SOCK_STREAM:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("127.0.0.1", port))
                except OSError as exc:
                    raise AppError(
                        "port_in_use",
                        f"端口 {port} 不可用。",
                        5,
                        "用 config set 指定空闲的代理端口和管理端口。",
                    ) from exc

    def healthy(self, record):
        request = urllib.request.Request(
            f"http://127.0.0.1:{record['settings']['controller_port']}/version",
            headers={"Authorization": f"Bearer {record['secret']}"},
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=0.3) as response:
                payload = json.load(response)
                if not isinstance(payload, dict) or not isinstance(payload.get("version"), str):
                    return False
            with socket.create_connection(
                ("127.0.0.1", record["settings"]["proxy_port"]), 0.3
            ) as sock:
                sock.sendall(b"\x05\x01\x00")
                return sock.recv(2) == b"\x05\x00"
        except (OSError, urllib.error.URLError, ValueError):
            return False

    def launch(self, compiled, name, settings, digest, secret):
        self.check_ports(settings)
        binary = self.executable()
        data_dir = str(self.data_dir(name))
        atomic_write(self.runtime_path, compiled)
        command = [binary, "-d", data_dir, "-f", str(self.runtime_path)]
        log_fd = os.open(self.log_path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        try:
            child = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                start_new_session=True,
            )
        finally:
            os.close(log_fd)
        record = {
            "pid": child.pid,
            "identity": process_identity(child.pid),
            "data_dir": data_dir,
            "subscription": name,
            "settings": settings,
            "fingerprint": digest,
            "secret": secret,
        }
        try:
            if record["identity"] is None:
                raise AppError(
                    "start_failed", "内核启动后立即退出。", suggestion="运行 core logs 查看日志。"
                )
            atomic_write(self.record_path, json.dumps(record))
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    break
                if self.healthy(record):
                    return record
                time.sleep(0.1)
            raise AppError(
                "start_failed",
                "内核未在期限内就绪。",
                suggestion="运行 core logs 查看日志。",
            )
        except BaseException:
            # This Popen handle belongs to us even if writing process.json failed.
            child.kill()
            child.wait()
            self.record_path.unlink(missing_ok=True)
            raise

    def replace(self, content, settings, name):
        self.validate(content, settings, name)
        old = self.running()
        previous = self.runtime_path.read_text() if old else None
        secret = secrets.token_urlsafe(32)
        compiled = render(content, settings, secret)
        # Check changed ports before stopping the working instance.
        if old:
            self.check_ports(settings, previous=old["settings"])
            self.stop()
        try:
            return self.launch(compiled, name, settings, fingerprint(content, settings), secret)
        except BaseException as failure:
            if old:
                try:
                    self.launch(
                        previous,
                        old["subscription"],
                        old["settings"],
                        old["fingerprint"],
                        old["secret"],
                    )
                except Exception as rollback:
                    raise AppError(
                        "rollback_failed",
                        "启动失败，恢复旧实例也失败。订阅状态未修改。",
                        suggestion="检查 core logs 后重新 core start。",
                    ) from rollback
            raise failure
