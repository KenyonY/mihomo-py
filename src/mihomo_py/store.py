import fcntl
import ipaddress
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .errors import AppError

DEFAULT_SETTINGS = {
    "host": "127.0.0.1",
    "controller_host": "0.0.0.0",
    "proxy_port": 7897,
    "controller_port": 9090,
    "mode": "rule",
}
LANGUAGE_OPTIONS = ("auto", "zh", "en")
DEFAULT_LANGUAGE = "auto"


def valid_host(host):
    if not isinstance(host, str):
        return False
    try:
        ipaddress.IPv4Address(host)
        return True
    except ValueError:
        return False


def proxy_address(settings):
    return "127.0.0.1" if settings["host"] == "0.0.0.0" else settings["host"]


def controller_address(settings):
    host = settings["controller_host"]
    return "127.0.0.1" if host == "0.0.0.0" else host


def valid_settings(settings):
    return (
        isinstance(settings, dict)
        and set(settings) == set(DEFAULT_SETTINGS)
        and valid_host(settings["host"])
        and valid_host(settings["controller_host"])
        and all(
            type(settings[key]) is int and 1 <= settings[key] <= 65535
            for key in ("proxy_port", "controller_port")
        )
        and settings["mode"] in ("rule", "global", "direct")
        and settings["proxy_port"] != settings["controller_port"]
    )


def atomic_write(path: Path, content: str):
    """Replace a private file without leaving truncated state after interruption."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # Rename is the commit point. No fallible filesystem work after it:
        # callers may roll back a running process when a save raises OSError.
        # File contents are fsynced; directory durability across power loss is
        # deliberately not promised (see README).
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


class Store:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()

    def read(self):
        path = self.root / "state.json"
        if not path.exists():
            return {
                "version": 1,
                "language": DEFAULT_LANGUAGE,
                "selected": None,
                "settings": dict(DEFAULT_SETTINGS),
                "subs": {},
            }
        try:
            state = json.loads(path.read_text())
            # Language preference was added without changing the state version.
            # Old state files follow the system language until the user chooses one.
            if isinstance(state, dict):
                state.setdefault("language", DEFAULT_LANGUAGE)
            # Released settings predate the configurable proxy / controller binds.
            if isinstance(state, dict) and isinstance(state.get("settings"), dict):
                state["settings"].setdefault("host", DEFAULT_SETTINGS["host"])
                state["settings"].setdefault("controller_host", DEFAULT_SETTINGS["controller_host"])
            if not (
                state["version"] == 1
                and state["language"] in LANGUAGE_OPTIONS
                and isinstance(state["subs"], dict)
                and (state["selected"] is None or state["selected"] in state["subs"])
                and valid_settings(state["settings"])
            ):
                raise ValueError
            for sub in state["subs"].values():
                if not all(
                    isinstance(sub[key], str) for key in ("source", "content", "updated_at")
                ):
                    raise ValueError
            return state
        except (ValueError, KeyError, TypeError) as exc:
            raise AppError("invalid_state", "state.json 损坏或版本不支持，请从备份恢复。") from exc

    def save(self, state):
        # Keeping source and cache in one transaction avoids mismatched metadata/cache files.
        atomic_write(
            self.root / "state.json", json.dumps(state, ensure_ascii=False, indent=2) + "\n"
        )

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.root / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AppError(
                    "busy", "另一个命令正在修改此实例，请稍后重试。", 5, retryable=True
                ) from exc
            yield
        finally:
            os.close(fd)
