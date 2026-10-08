"""Seed missing public geodata files without sharing mutable core state."""

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

import maxminddb

from .errors import AppError

# Filename aliases recognized by mihomo's constant/path.go.
ASSETS = {
    "mmdb": ("country.mmdb", "geoip.db", "geoip.metadb"),
    "geoip": ("geoip.dat",),
    "geosite": ("geosite.dat",),
    "asn": ("asn.mmdb",),
}


def valid_asset(path, kind):
    if path.stat().st_size == 0:
        return False
    if kind in ("mmdb", "asn"):
        try:
            with maxminddb.open_database(path, mode=maxminddb.MODE_FILE) as reader:
                reader.metadata()
        except maxminddb.InvalidDatabaseError:
            return False
    return True


def check_geodata(directory):
    for path in directory.iterdir():
        kind = next(
            (kind for kind, aliases in ASSETS.items() if path.name.lower() in aliases), None
        )
        if kind and path.is_file() and not valid_asset(path, kind):
            raise AppError(
                "invalid_geodata",
                f"内核下载的地理数据库 {path.name} 无效；订阅尚未保存。",
                suggestion="检查 geox-url 来源返回的数据库文件是否完整。",
            )


def copy_asset(source, destination):
    fd, temporary = tempfile.mkstemp(prefix=".geo-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as input_stream:
            shutil.copyfileobj(input_stream, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def geodata_transaction(directory):
    """Restore GEO files if validation fails, including files the core downloaded."""
    names = {name for aliases in ASSETS.values() for name in aliases}

    def files():
        return [
            path for path in directory.iterdir() if path.name.lower() in names and path.is_file()
        ]

    with tempfile.TemporaryDirectory(prefix=".geo-backup-", dir=directory) as temporary:
        backup = Path(temporary)
        original = files()
        for path in original:
            copy_asset(path, backup / path.name)
        try:
            yield
        except BaseException:
            for path in files():
                path.unlink()
            for path in original:
                copy_asset(backup / path.name, path)
            raise


def seed_geodata(root, destination, config):
    configured = os.environ.get("MIHOMO_PY_GEODATA_DIR")
    if configured:
        source = Path(configured).expanduser()
        if not source.is_dir():
            raise AppError("invalid_geodata_dir", "MIHOMO_PY_GEODATA_DIR 不是可读取的目录。", 2)
        sources = [source]
    else:
        # Match mihomo's own home-directory selection; do not read its config or cache.db.
        core_home = Path.home() / ".config" / "mihomo"
        xdg = os.environ.get("XDG_CONFIG_HOME")
        if not core_home.exists() and xdg and Path(xdg).is_absolute():
            core_home = Path(xdg) / "mihomo"
        sources = [root / "geodata", core_home]
    for path in destination.iterdir():
        kind = next(
            (kind for kind, aliases in ASSETS.items() if path.name.lower() in aliases), None
        )
        if kind and path.is_file() and not valid_asset(path, kind):
            path.unlink()
    existing = {path.name.lower() for path in destination.iterdir()}
    custom = config.get("geox-url") or {}
    custom = custom if isinstance(custom, dict) else {}
    copied = []
    for source in sources:
        if not source.is_dir():
            continue
        available = {path.name.lower(): path for path in source.iterdir() if path.is_file()}
        for kind, aliases in ASSETS.items():
            # A subscription's custom datasets must not be replaced with unrelated defaults.
            if custom.get(kind) or any(name in existing for name in aliases):
                continue
            candidate = next(
                (
                    available[name]
                    for name in aliases
                    if name in available and valid_asset(available[name], kind)
                ),
                None,
            )
            if candidate is None:
                continue
            copy_asset(candidate, destination / candidate.name)
            existing.add(candidate.name.lower())
            copied.append(candidate.name)
    return copied
