import hashlib
import json
import os
import platform
import subprocess

import pytest

from mihomo_py.bundle import ASSET_ROOT, core_path
from mihomo_py.config import render
from mihomo_py.engine import Engine
from mihomo_py.errors import AppError
from mihomo_py.geodata import ASSETS, seed_geodata
from mihomo_py.store import DEFAULT_SETTINGS


def test_bundled_core_is_default_even_when_path_has_another_core(tmp_path, monkeypatch):
    executable = tmp_path / "mihomo"
    executable.write_text("#!/bin/sh\nexit 99\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert Engine(tmp_path).executable() == str(core_path().resolve())
    assert Engine(tmp_path, "mihomo").executable() == str(executable)
    with pytest.raises(AppError) as exc:
        Engine(tmp_path, "missing-core").executable()
    assert exc.value.kind == "core_missing"


def test_bundle_hashes_and_real_version():
    manifest = json.loads((ASSET_ROOT / "manifest.json").read_text())
    for asset in manifest["assets"]:
        path = ASSET_ROOT / asset["path"]
        if asset["path"].endswith("/mihomo") and not path.exists():
            assert asset["path"] != f"{platform.machine()}/mihomo"
            continue  # Wheels contain exactly one architecture.
        assert hashlib.sha256(path.read_bytes()).hexdigest() == asset["sha256"]
    result = subprocess.run([core_path(), "-v"], capture_output=True, text=True, check=True)
    assert manifest["core_version"] in result.stdout


@pytest.mark.parametrize("geodata_mode", [False, True])
def test_real_core_validates_all_bundled_datasets(tmp_path, monkeypatch, geodata_mode):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("MIHOMO_PY_GEODATA_DIR", raising=False)
    # A download attempt fails immediately, including attempts made by the Go subprocess.
    urls = {key: "http://127.0.0.1:1/unavailable" for key in ASSETS}
    content = (
        "proxies: []\nrules: ['GEOSITE,cn,DIRECT', 'GEOIP,CN,DIRECT', "
        "'IP-ASN,13335,DIRECT', 'MATCH,DIRECT']\n"
        f"geodata-mode: {str(geodata_mode).lower()}\n"
    )
    engine = Engine(tmp_path / "state", timeout=10)
    directory = engine.data_dir("offline")
    copied = seed_geodata(engine.root, directory, {})
    assert set(copied) == {"country.mmdb", "geoip.dat", "geosite.dat", "ASN.mmdb"}
    import yaml

    config = yaml.safe_load(render(content, DEFAULT_SETTINGS, "test-only"))
    config["geox-url"] = urls
    config_path = directory / "test.yaml"
    config_path.write_text(yaml.safe_dump(config))
    result = subprocess.run(
        [core_path(), "-t", "-d", directory, "-f", config_path],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "download" not in result.stdout.lower()


def test_custom_geodata_is_not_silently_replaced(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("MIHOMO_PY_GEODATA_DIR", raising=False)
    target = tmp_path / "target"
    target.mkdir()
    custom = {"geox-url": {kind: "https://custom.invalid/data" for kind in ASSETS}}
    assert seed_geodata(tmp_path, target, custom) == []
    # An explicitly supplied directory can satisfy custom URLs offline.
    monkeypatch.setenv("MIHOMO_PY_GEODATA_DIR", str(ASSET_ROOT / "geodata"))
    assert len(seed_geodata(tmp_path, target, custom)) == 4


def test_bundled_data_is_copied_without_mutating_package(tmp_path, monkeypatch):
    monkeypatch.setenv("MIHOMO_PY_GEODATA_DIR", str(ASSET_ROOT / "geodata"))
    target = tmp_path / "target"
    target.mkdir()
    seed_geodata(tmp_path, target, {})
    original = ASSET_ROOT / "geodata/country.mmdb"
    assert (target / "country.mmdb").read_bytes() == original.read_bytes()
    assert os.stat(target / "country.mmdb").st_ino != os.stat(original).st_ino
    (target / "country.mmdb").write_bytes(b"changed")
    assert original.stat().st_size > 1000


def test_geo_auto_update_cannot_break_offline_bootstrap():
    import yaml

    config = yaml.safe_load(render("proxies: []\ngeo-auto-update: true", DEFAULT_SETTINGS, "s"))
    assert config["geo-auto-update"] is False
