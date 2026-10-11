"""Explicit, per-process TUN authorization without changing file capabilities."""

import os
import pwd
import re
import sys
from pathlib import Path

from . import tun
from .errors import AppError

REEXEC = "_MIHOMO_PY_SUDO_ACTIVE"


def ready():
    if not tun.permission()["cap_net_admin"]:
        return False
    if os.geteuid() == 0:
        return True
    # Ambient capability survives exec into the core and other child commands.
    status = Path("/proc/self/status").read_text()
    ambient = re.search(r"^CapAmb:\s*(\w+)", status, re.M)
    return bool(ambient and int(ambient[1], 16) & (1 << 12))


def system_tool(name):
    # These programs execute before privileges are dropped. Do not search user PATH.
    for directory in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        path = Path(directory) / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    raise AppError(
        "sudo_unavailable", "--sudo 需要系统安装 sudo 和 capsh。", 4,
        "请管理员安装 sudo 与 libcap 工具（Debian/Ubuntu：libcap2-bin）。",
    )


def authorize(context):
    root = context.find_root()
    if not root.params.get("sudo_mode") or context.params.get("dry_run"):
        return
    if sys.platform != "linux":
        raise AppError("tun_unsupported_platform", "--sudo 仅支持 Linux。", 2)
    if ready():
        return
    if os.environ.get(REEXEC) == "1":
        raise AppError(
            "sudo_permission_denied", "授权后仍未获得可传递给内核的 CAP_NET_ADMIN。", 4,
            "请管理员检查 sudo 策略、容器权限和 capability bounding set。",
        )
    user = pwd.getpwuid(os.getuid())
    sudo, capsh, env = (system_tool(name) for name in ("sudo", "capsh", "env"))
    arguments = [sudo, "-E"]
    if not sys.stdin.isatty():
        arguments.append("-n")
    arguments.extend([
        "--", capsh, "--keep=1", f"--user={user.pw_name}",
        "--caps=cap_net_admin+eip", "--addamb=cap_net_admin", "--shell=/bin/sh",
        "--", "-c", 'cd "$1" && shift && exec "$@"', "mihomo-py", os.getcwd(),
        env, f"{REEXEC}=1", f"HOME={os.environ.get('HOME', user.pw_dir)}",
        f"PATH={os.environ.get('PATH', os.defpath)}",
        # Preserve the virtualenv interpreter path, including its symlink.
        os.path.abspath(sys.executable), "-m", "mihomo_py",
        "--data-dir", str(Path(root.params["data_dir"]).expanduser().absolute()),
    ])
    if root.params.get("core_binary") is not None:
        arguments.extend(["--core-binary", root.params["core_binary"]])
    arguments.extend(root.meta["argv"])
    # sudo owns the password prompt. The application never reads or stores it.
    os.execv(sudo, arguments)
