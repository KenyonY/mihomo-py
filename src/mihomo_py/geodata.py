"""Seed missing public geodata files without sharing mutable core state."""

import os
import shutil
import tempfile
from pathlib import Path

from .errors import AppError

# Filename aliases recognized by mihomo's constant/path.go.
ASSETS = {
    "mmdb": ("country.mmdb", "geoip.db", "geoip.metadb"),
    "geoip": ("geoip.dat",),
    "geosite": ("geosite.dat",),
    "asn": ("asn.mmdb",),
}


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
            candidate = next((available[name] for name in aliases if name in available), None)
            if candidate is None or candidate.stat().st_size == 0:
                continue
            fd, temporary = tempfile.mkstemp(prefix=".geo-", dir=destination)
            try:
                with os.fdopen(fd, "wb") as output, candidate.open("rb") as input_stream:
                    shutil.copyfileobj(input_stream, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination / candidate.name)
                existing.add(candidate.name.lower())
                copied.append(candidate.name)
            finally:
                Path(temporary).unlink(missing_ok=True)
    return copied
