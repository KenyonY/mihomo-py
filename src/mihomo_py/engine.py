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
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import pidfd, tun
from .bundle import core_path, seed_dashboard
from .config import parse, render
from .errors import AppError, Message
from .geodata import check_geodata, geodata_transaction, seed_geodata
from .store import (
    DEFAULT_SETTINGS,
    atomic_write,
    controller_address,
    proxy_address,
    upgrade_settings,
    valid_settings,
)


def reap_process(process):
    """Reap a detached child without tying its lifetime to the CLI/TUI process."""
    threading.Thread(
        target=process.wait,
        name=f"mihomo-py-reaper-{process.pid}",
        daemon=True,
    ).start()


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


def command_matches(cmdline, command):
    """Match the core command, including the documented binfmt QEMU argv wrapper."""
    if cmdline == command:
        return True
    wrapper = Path(cmdline[0]).name if cmdline else ""
    known_wrappers = {"qemu-aarch64", "qemu-arm", "qemu-x86_64", "qemu-i386"}
    if wrapper in known_wrappers and cmdline[1:] == command:
        return True
    if len(cmdline) < len(command) + 2:
        return False
    if wrapper not in known_wrappers:
        return False
    return cmdline[-len(command) :] == command and cmdline[-len(command) - 1] == command[0]


def command_from_cmdline(cmdline):
    wrapper = Path(cmdline[0]).name if cmdline else ""
    if wrapper in {"qemu-aarch64", "qemu-arm", "qemu-x86_64", "qemu-i386"}:
        command = cmdline[1:]
        if len(command) > 1 and command[0] == command[1]:
            command = command[1:]
        return command
    return cmdline


def identity_matches(identity, expected, data_dir=None, runtime_path=None):
    command = command_from_cmdline(expected["cmdline"]) if expected else None
    command_args = ["-d", data_dir, "-f", str(runtime_path)] if data_dir else None
    return (
        identity
        and expected
        and identity["start_ticks"] == expected["start_ticks"]
        and identity["boot_id"] == expected["boot_id"]
        and command
        and (command_args is None or command[-4:] == command_args)
        and command_matches(identity["cmdline"], command)
    )


class Engine:
    def __init__(self, root, binary=None, timeout=20):
        self.root = root
        self.binary = binary
        self.timeout = timeout
        self.record_path = root / "process.json"
        self.runtime_path = root / "runtime.yaml"
        self.log_path = root / "core.log"

    def executable(self):
        self.require_process_api()
        binary = shutil.which(self.binary) if self.binary else str(core_path())
        if not binary:
            raise AppError(
                "core_missing",
                "找不到 mihomo 内核。",
                3,
                "检查 --core-binary / MIHOMO_PY_BINARY；省略时使用包内 mihomo。",
            )
        return str(Path(binary).resolve())

    @staticmethod
    def require_process_api():
        if sys.platform != "linux":
            raise AppError("unsupported_platform", "进程管理需要支持 pidfd 的 Linux 环境。")
        try:
            descriptor = pidfd.open_pidfd(os.getpid())
            try:
                # Signal 0 checks availability/permissions without delivering a signal.
                pidfd.send_signal(descriptor, 0)
            finally:
                os.close(descriptor)
        except OSError as exc:
            raise AppError(
                "unsupported_platform",
                Message("当前系统无法使用 pidfd 进程控制（errno={errno}）。", errno=exc.errno),
                suggestion="需要 Linux 5.3+ 且容器允许 pidfd 系统调用；"
                "Python 缺少原生接口时，兼容层支持 x86_64/aarch64 64 位环境。",
            ) from exc

    def data_path(self, name, config=None):
        # Providers and selector caches from different subscriptions must not collide.
        custom = (config or {}).get("geox-url") or {}
        identity = name
        if custom:
            identity += "\0" + json.dumps(custom, sort_keys=True)
        key = hashlib.sha256(identity.encode()).hexdigest()[:16]
        return self.root / "core-data" / key

    def data_dir(self, name, config=None):
        directory = self.data_path(name, config)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory

    def validate(self, content, settings, name, *, progress=None):
        binary = self.executable()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        config = parse(content)
        data_dir = self.data_dir(name, config)
        with geodata_transaction(data_dir):
            self._validate(content, settings, binary, data_dir, config, progress)

    def _validate(self, content, settings, binary, data_dir, config, progress):
        if progress:
            progress("订阅已读取，正在准备地理数据…")
        copied = seed_geodata(self.root, data_dir, config)
        dashboard = seed_dashboard(data_dir)
        if progress:
            progress(
                Message("已复用 {count} 个地理数据文件，正在校验配置…", count=len(copied))
                if copied else "订阅已读取，正在校验配置…"
            )
        fd, temporary = tempfile.mkstemp(prefix=".check-", suffix=".yaml", dir=self.root)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(render(content, settings, "validation-only", dashboard=dashboard))
            try:
                result = subprocess.run(
                    [binary, "-t", "-d", str(data_dir), "-f", temporary],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    timeout=self.timeout,
                )
            except subprocess.TimeoutExpired as exc:
                diagnostics = (exc.stdout or b"").decode(errors="replace")
                atomic_write(self.root / "validation.log", diagnostics)
                raise AppError(
                    "validation_timeout",
                    "订阅已读取，但内核配置校验超时；订阅尚未保存，原配置未替换。",
                    suggestion=Message(
                        "默认地理数据已随包提供；检查订阅的自定义数据或规则源是否可达。"
                        "可通过 MIHOMO_PY_GEODATA_DIR 提供自定义离线地理数据；详见 {path}。",
                        path=self.root / "validation.log",
                    ),
                    retryable=True,
                ) from exc
            if result.returncode:
                # Keep raw diagnostics private: the core can echo subscription credentials.
                atomic_write(self.root / "validation.log", result.stdout.decode(errors="replace"))
                raise AppError(
                    "validation_failed",
                    "mihomo 拒绝此配置，原配置未替换。",
                    2,
                    Message("查看本地诊断文件：{path}", path=self.root / "validation.log"),
                )
            # mihomo can exit successfully after downloading an invalid MMDB.
            try:
                check_geodata(data_dir)
            except AppError as exc:
                atomic_write(
                    self.root / "validation.log",
                    result.stdout.decode(errors="replace") + "\n" + str(exc),
                )
                raise
        finally:
            Path(temporary).unlink(missing_ok=True)

    def record(self):
        if not self.record_path.exists():
            return None
        try:
            record = json.loads(self.record_path.read_text())
            if isinstance(record, dict) and isinstance(record.get("settings"), dict):
                record["settings"].setdefault("host", DEFAULT_SETTINGS["host"])
                record["settings"].setdefault(
                    "controller_host", "127.0.0.1"  # Old cores actually bound loopback.
                )
                upgrade_settings(record["settings"])
            if type(record["pid"]) is not int or record["pid"] <= 1:
                raise ValueError
            state = record.get("state", "running")
            identity = record["identity"]
            tun_resources = record.get("tun_resources")
            command = record.get("command")
            valid_identity = (
                isinstance(identity, dict)
                and all(isinstance(identity[key], str) for key in ("start_ticks", "boot_id"))
                and isinstance(identity["cmdline"], list)
                and all(isinstance(value, str) for value in identity["cmdline"])
            )
            valid_snapshot = (
                tun_resources is None
                or tun.valid_snapshot(tun_resources, record["settings"])
            )
            valid_command = (
                isinstance(command, list)
                and len(command) == 5
                and all(isinstance(value, str) and value for value in command)
                and command[1:] == [
                    "-d", record["data_dir"], "-f", str(self.runtime_path)
                ]
            )
            if not (
                state in ("starting", "running")
                and (valid_identity or state == "starting" and identity is None)
                and (state == "running" or valid_command)
                and all(
                    isinstance(record[key], str)
                    for key in ("data_dir", "subscription", "fingerprint", "secret")
                )
                and valid_settings(record["settings"])
                and valid_snapshot
            ):
                raise ValueError
            return record
        except (ValueError, KeyError, TypeError) as exc:
            raise AppError(
                "invalid_process_state", "process.json 损坏，无法确认进程身份。"
            ) from exc

    def running(self):
        record = self.record()
        # Do not trust a PID alone, even if it now points at another mihomo instance.
        expected = record["identity"] if record else None
        if record and record.get("state", "running") == "running" and identity_matches(
            process_identity(record["pid"]), expected, record["data_dir"], self.runtime_path
        ):
            return record
        return None

    @tun.serialized
    def stop(self):
        record = self.running()
        was_starting = False
        if not record:
            stale = self.record()
            if stale and stale.get("state") == "starting":
                identity = process_identity(stale["pid"])
                if identity:
                    expected = stale["identity"]
                    confirmed = (
                        identity_matches(
                            identity, expected, stale["data_dir"], self.runtime_path
                        )
                        if expected
                        else command_matches(identity["cmdline"], stale["command"])
                    )
                    if not confirmed:
                        raise AppError(
                            "unconfirmed_starting_process",
                            "启动中的进程身份无法确认，已保留恢复记录。",
                            5,
                            "检查 core logs 和进程命令行；不要按 PID 盲目终止其他进程。",
                        )
                    if expected is None:
                        stale["identity"] = identity
                        atomic_write(self.record_path, json.dumps(stale))
                    record = stale
                    was_starting = True
            if record is None:
                if stale and stale.get("state") == "starting":
                    self.capture_starting_tun(stale)
                tun.cleanup(stale)
                self.record_path.unlink(missing_ok=True)
                return False
        self.require_process_api()
        try:
            fd = pidfd.open_pidfd(record["pid"])
        except ProcessLookupError:
            if was_starting:
                self.capture_starting_tun(record)
            tun.cleanup(record)
            self.record_path.unlink(missing_ok=True)
            return False
        try:
            if not identity_matches(
                process_identity(record["pid"]),
                record["identity"],
                record["data_dir"],
                self.runtime_path,
            ):
                if was_starting:
                    self.capture_starting_tun(record)
                tun.cleanup(record)
                self.record_path.unlink(missing_ok=True)
                return False
            pidfd.send_signal(fd, signal.SIGTERM)
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            if not poller.poll(5000):
                pidfd.send_signal(fd, signal.SIGKILL)
                if not poller.poll(3000):
                    raise AppError("stop_failed", "内核尚未退出，请检查进程状态。")
        except ProcessLookupError:
            pass
        finally:
            os.close(fd)
        if was_starting:
            self.capture_starting_tun(record)
        tun.cleanup(record)
        self.record_path.unlink(missing_ok=True)
        return True

    def capture_starting_tun(self, record):
        if not record["settings"].get("tun", tun.DEFAULT_TUN)["enabled"]:
            return
        snapshot = tun.require_owned_snapshot(
            record, tun.settled_network(record), {"routes": [], "rules": []}
        )
        record["tun_resources"] = snapshot
        atomic_write(self.record_path, json.dumps(record))

    @staticmethod
    def listeners(settings):
        return {
            (settings["host"], socket.SOCK_STREAM, settings["proxy_port"]),
            (settings["host"], socket.SOCK_DGRAM, settings["proxy_port"]),
            (settings["controller_host"], socket.SOCK_STREAM, settings["controller_port"]),
        }

    def check_ports(self, settings, previous=None):
        owned = self.listeners(previous) if previous else set()
        for host, kind, port in self.listeners(settings):
            # A wildcard and a specific bind overlap. Recheck after stopping our core.
            if any(
                kind == old_kind and port == old_port
                and (host == old_host or "0.0.0.0" in (host, old_host))
                for old_host, old_kind, old_port in owned
            ):
                continue
            with socket.socket(type=kind) as sock:
                if kind == socket.SOCK_STREAM:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind((host, port))
                except OSError as exc:
                    raise AppError(
                        "port_in_use",
                        Message("端口 {port} 不可用。", port=port),
                        5,
                        "用 config set 指定空闲的代理端口和管理端口。",
                    ) from exc

    def healthy(self, record):
        timeout = float(os.environ.get("MIHOMO_PY_HEALTHY_TIMEOUT", "0.3"))
        request = urllib.request.Request(
            f"http://{controller_address(record['settings'])}:"
            f"{record['settings']['controller_port']}/version",
            headers={"Authorization": f"Bearer {record['secret']}"},
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=timeout) as response:
                payload = json.load(response)
                if not isinstance(payload, dict) or not isinstance(payload.get("version"), str):
                    return False
            with socket.create_connection(
                (proxy_address(record["settings"]), record["settings"]["proxy_port"]), timeout
            ) as sock:
                sock.sendall(b"\x05\x01\x00")
                return sock.recv(2) == b"\x05\x00"
        except urllib.error.HTTPError as exc:
            exc.close()
            return False
        except (OSError, urllib.error.URLError, ValueError):
            return False

    @tun.serialized
    def launch(self, compiled, name, settings, digest, secret, *, data_directory=None):
        tun_enabled = settings.get("tun", tun.DEFAULT_TUN)["enabled"]
        baseline = {"routes": [], "rules": []}
        if tun_enabled:
            baseline = tun.resources(tun.preflight(settings["tun"]))
        self.check_ports(settings)
        binary = self.executable()
        data_dir = str(data_directory or self.data_dir(name, parse(compiled)))
        atomic_write(self.runtime_path, compiled)
        command = [binary, "-d", data_dir, "-f", str(self.runtime_path)]
        log_fd = os.open(self.log_path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
        child = None
        record = {
            "pid": None,
            "identity": None,
            "state": "starting",
            "command": command,
            "data_dir": data_dir,
            "subscription": name,
            "settings": settings,
            "fingerprint": digest,
            "secret": secret,
        }

        def save_tun_resources(net):
            snapshot, clean = tun.owned_snapshot(record, net, baseline)
            if record.get("tun_resources") != snapshot:
                record["tun_resources"] = snapshot
                atomic_write(self.record_path, json.dumps(record))
            if not clean:
                tun.require_owned_snapshot(record, net, baseline)

        try:
            child = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                start_new_session=True,
            )
            record["pid"] = child.pid
            if tun_enabled:
                record["tun_resources"] = {"routes": [], "rules": []}
            atomic_write(self.record_path, json.dumps(record))
            deadline = time.monotonic() + self.timeout
            # /proc may briefly expose an empty cmdline immediately after exec.
            # Persist only a complete identity, or later stop/status cannot recognize it.
            while time.monotonic() < deadline and child.poll() is None:
                identity = process_identity(child.pid)
                if identity and command_matches(identity["cmdline"], command):
                    record["identity"] = identity
                    if not tun_enabled:
                        record["state"] = "running"
                    atomic_write(self.record_path, json.dumps(record))
                    break
                time.sleep(0.01)
            if record["identity"] is None:
                raise AppError(
                    "start_failed",
                    "内核退出或未能确认启动身份。",
                    suggestion="运行 core logs 查看日志。",
                )
            os.close(log_fd)
            log_fd = None
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    if tun_enabled:
                        save_tun_resources(tun.settled_network(record))
                    break
                net = tun.network() if tun_enabled else None
                if tun_enabled:
                    save_tun_resources(net)
                if self.healthy(record) and (
                    not tun_enabled or tun.observation(record, net)["active"]
                ):
                    if tun_enabled:
                        # A TUN process is recoverable as "running" only after the
                        # complete, healthy resource snapshot is durably recorded.
                        record["state"] = "running"
                        atomic_write(self.record_path, json.dumps(record))
                    reap_process(child)
                    return record
                time.sleep(0.1)
            raise AppError(
                "start_failed",
                "内核未在期限内就绪。",
                suggestion="运行 core logs 查看日志。",
            )
        except BaseException as failure:
            if log_fd is not None:
                try:
                    os.close(log_fd)
                except OSError:
                    pass
            if child is None:
                raise
            # This Popen handle belongs to us from the instant creation succeeds.
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            if tun_enabled:
                save_tun_resources(tun.settled_network(record))
            tun.cleanup(record)
            self.record_path.unlink(missing_ok=True)
            if tun_enabled and isinstance(failure, AppError) and failure.kind == "start_failed":
                raise AppError("tun_start_failed", "TUN 内核未就绪。", 1,
                               "运行 tun doctor 和 core logs 检查权限、路由与 DNS。",
                               True) from failure
            raise

    def controller_secret(self):
        """One private credential for the controller and the optional Web manager."""
        secret_path = self.root / "controller-secret"
        if secret_path.exists():
            secret = secret_path.read_text().strip()
            if not secret or any(character.isspace() for character in secret):
                raise AppError("invalid_secret", "controller-secret 无效，请恢复备份。")
        else:
            old = self.running()
            secret = old["secret"] if old else secrets.token_urlsafe(32)
            atomic_write(secret_path, secret + "\n")
        return secret

    def replace(self, content, settings, name):
        self.validate(content, settings, name)
        secret = self.controller_secret()
        dashboard = seed_dashboard(self.data_dir(name, parse(content)))
        compiled = render(content, settings, secret, dashboard=dashboard)
        return self.replace_runtime(
            compiled, name, settings, fingerprint(content, settings), secret
        )

    @tun.serialized
    def replace_runtime(self, compiled, name, settings, digest, secret):
        """Replace an already validated runtime, restoring the previous core on failure."""
        old = self.running()
        if not old:
            tun.check_cleanup(self.record())
        if settings.get("tun", tun.DEFAULT_TUN)["enabled"]:
            tun.preflight(settings["tun"], old)
        elif old and old["settings"].get("tun", tun.DEFAULT_TUN)["enabled"]:
            # Disabling TUN still needs network inspection to verify cleanup.
            # Discover an unavailable iproute2 before stopping the working core.
            tun.network()
        previous = self.runtime_path.read_text() if old else None
        # Check changed ports before stopping the working instance.
        if old:
            self.check_ports(settings, previous=old["settings"])
        try:
            if old:
                self.stop()
            return self.launch(compiled, name, settings, digest, secret)
        except BaseException as failure:
            if old and self.running() != old:
                self.restore_runtime(
                    previous, old["subscription"], old["settings"], old["fingerprint"],
                    old["secret"], data_directory=old["data_dir"],
                )
            raise failure

    @tun.serialized
    def restore_runtime(self, compiled, name, settings, digest, secret, *, data_directory):
        """Restore after replacement or persistence failure, retaining unclean TUN evidence."""
        try:
            try:
                self.stop()
            except Exception:
                if self.running():
                    raise
                # A transient inspection error after exit can be retried below.
            # Check the failed replacement, including when restoring a plain core.
            tun.check_cleanup(self.record())
            return self.launch(
                compiled, name, settings, digest, secret, data_directory=data_directory,
            )
        except Exception as rollback:
            raise AppError(
                "rollback_failed",
                "恢复旧实例失败。订阅状态未修改。",
                suggestion="运行 tun doctor 和 core logs 检查后重新 core start。",
            ) from rollback
