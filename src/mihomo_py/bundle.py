"""Immutable resources shipped by pip, shared by all client instances."""

import platform
from pathlib import Path

from .errors import AppError

ASSET_ROOT = Path(__file__).with_name("_vendor")


def core_path():
    path = ASSET_ROOT / platform.machine() / "mihomo"
    if not path.is_file():
        raise AppError(
            "core_missing",
            "安装包缺少当前架构的 mihomo 内核。",
            3,
            "请从 pip 镜像源重新安装完整发行包，或用 --core-binary 指定内核。",
        )
    return path
