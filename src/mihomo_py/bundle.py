"""Immutable resources shipped by pip, shared by all client instances."""

import hashlib
import json
import platform
import shutil
import tempfile
import zipfile
from importlib.util import find_spec
from pathlib import Path

from .errors import AppError

ASSET_ROOT = Path(__file__).with_name("_vendor")


def dashboard_root():
    spec = find_spec("mihomo_py_web")
    return Path(spec.origin).parent / "_vendor" if spec else None


def seed_dashboard(data_dir):
    """Install the pinned static UI inside mihomo's safe working directory, offline."""
    root = dashboard_root()
    if root is None:
        return None
    manifest = json.loads((root / "manifest.json").read_text())
    asset = next(item for item in manifest["assets"] if item["path"] == "zashboard.zip")
    bootstrap = root.parent.joinpath("web_bootstrap.js").read_bytes()
    name = "ui-" + hashlib.sha256(asset["sha256"].encode() + bootstrap).hexdigest()[:16]
    target = data_dir / name
    if (target / "index.html").is_file():
        return name
    archive_path = root / "zashboard.zip"
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != asset["sha256"]:
        raise AppError("invalid_dashboard", "包内 Web 面板校验失败，请重新安装发行包。")
    with tempfile.TemporaryDirectory(prefix=".ui-", dir=data_dir) as temporary:
        directory = Path(temporary)
        with zipfile.ZipFile(archive_path) as archive:
            # The hash pins this exact upstream archive, whose top-level directory is dist/.
            for member in archive.infolist():
                relative = Path(member.filename).relative_to("dist")
                if member.is_dir():
                    continue
                destination = directory / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
        if not (directory / "index.html").is_file():
            raise AppError("invalid_dashboard", "包内 Web 面板缺少入口文件。")
        index = directory / "index.html"
        # Restrict the embedded dashboard to this core, including WebSocket streams.
        index.write_text(index.read_text().replace(
            "<head>",
            '<head>\n<meta http-equiv="Content-Security-Policy" '
            'content="connect-src \'self\'; img-src \'self\' data: blob:; font-src \'self\'">'
            '\n<script src="./mihomo-py.js"></script>',
            1,
        ))
        (directory / "mihomo-py.js").write_bytes(bootstrap)
        if target.exists():
            shutil.rmtree(target)
        directory.rename(target)
    return name


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
