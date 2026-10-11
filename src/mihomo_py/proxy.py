"""Shell proxy output and argv-only command execution."""

import os
import shlex
import subprocess

from .errors import AppError
from .store import proxy_address

DEFAULT_NO_PROXY = "localhost,127.0.0.1,::1"
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


def environment(manager, no_proxy=None):
    running = manager.engine.running()
    saved = manager.store.read()["settings"]
    settings = running["settings"] if running else saved
    host, port = proxy_address(settings), settings["proxy_port"]
    variables = dict(
        zip(
            PROXY_KEYS,
            (
                f"http://{host}:{port}",
                f"http://{host}:{port}",
                f"socks5h://{host}:{port}",
                saved.get("no_proxy", DEFAULT_NO_PROXY) if no_proxy is None else no_proxy,
            ),
        )
    )
    variables.update({key.lower(): value for key, value in list(variables.items())})
    return {"running": bool(running), "host": host, "port": port, "variables": variables}


def shell_statements(variables, shell, unset=False):
    lines = []
    for key, value in variables.items():
        if unset:
            lines.append(f"set -e {key};" if shell == "fish" else f"unset {key};")
        elif shell == "fish":
            # Fish single quotes only interpret escaped backslashes and apostrophes.
            quoted = "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
            lines.append(f"set -gx {key} {quoted};")
        else:
            lines.append(f"export {key}={shlex.quote(value)};")
    return "\n".join(lines)


def run(manager, command, no_proxy=None):
    record = manager.engine.running()
    if not record or not manager.engine.healthy(record):
        raise AppError("core_stopped", "内核未运行或不健康。", 3, "先运行 core start。")
    values = environment(manager, no_proxy)["variables"]
    environ = {k: v for k, v in os.environ.items() if k.upper() not in PROXY_KEYS}
    environ.update(values)
    try:
        child = subprocess.Popen(command, env=environ)
    except FileNotFoundError as exc:
        raise AppError(
            "command_not_found", "找不到要执行的命令。", 127, "检查命令及 PATH。"
        ) from exc
    except PermissionError as exc:
        raise AppError("command_not_executable", "命令不可执行。", 126, "检查执行权限。") from exc
    try:
        code = child.wait()
    except KeyboardInterrupt:
        # The foreground process group also receives Ctrl-C; always reap the child.
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        return 130
    return code if code >= 0 else 128 - code
