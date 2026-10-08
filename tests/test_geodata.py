import sys
import urllib.error

import pytest

from mihomo_py.config import fetch
from mihomo_py.errors import AppError
from mihomo_py.geodata import seed_geodata
from mihomo_py.manager import Manager


def test_seed_geodata_copies_only_known_assets_and_preserves_existing(tmp_path, monkeypatch):
    source = tmp_path / "assets"
    source.mkdir()
    monkeypatch.setenv("MIHOMO_PY_GEODATA_DIR", str(source))
    for name in ("GeoSite.dat", "Country.mmdb", "GeoIP.dat", "ASN.mmdb", "config.yaml", "cache.db"):
        (source / name).write_bytes(b"original")
    target = tmp_path / "target"
    target.mkdir()
    (target / "geoip.metadb").write_bytes(b"existing")
    copied = seed_geodata(tmp_path, target, {"geox-url": {"geosite": "https://custom.invalid"}})
    assert set(copied) == {"GeoIP.dat", "ASN.mmdb"}
    assert (target / "geoip.metadb").read_bytes() == b"existing"
    assert not (target / "Country.mmdb").exists()
    assert not (target / "GeoSite.dat").exists()
    assert not (target / "cache.db").exists()
    assert not (target / "config.yaml").exists()
    assert (target / "ASN.mmdb").stat().st_mode & 0o777 == 0o600
    (target / "ASN.mmdb").write_bytes(b"changed")
    assert (source / "ASN.mmdb").read_bytes() == b"original"
    assert not list(target.glob(".geo-*"))


def test_seed_default_directory_priority_and_case_aliases(tmp_path, monkeypatch):
    monkeypatch.delenv("MIHOMO_PY_GEODATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "client"
    shared = root / "geodata"
    shared.mkdir(parents=True)
    (shared / "geoip.metadb").write_bytes(b"client dataset")
    core_home = tmp_path / ".config" / "mihomo"
    core_home.mkdir(parents=True)
    (core_home / "Country.mmdb").write_bytes(b"core dataset")
    (core_home / "GEOSITE.DAT").write_bytes(b"site dataset")
    target = root / "target"
    target.mkdir()
    seed_geodata(root, target, {})
    assert (target / "geoip.metadb").read_bytes() == b"client dataset"
    assert (target / "GEOSITE.DAT").read_bytes() == b"site dataset"
    assert not (target / "Country.mmdb").exists()


def test_missing_explicit_asset_directory_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setenv("MIHOMO_PY_GEODATA_DIR", str(tmp_path / "missing"))
    with pytest.raises(AppError, match="MIHOMO_PY_GEODATA_DIR"):
        seed_geodata(tmp_path, tmp_path, {})


@pytest.mark.parametrize("wrapped", [False, True])
def test_subscription_timeout_retries_once_and_remains_distinct(monkeypatch, wrapped):
    calls = []
    progress = []

    class Opener:
        def open(self, *args, **kwargs):
            calls.append(1)
            error = TimeoutError("private-subscription-token")
            raise urllib.error.URLError(error) if wrapped else error

    monkeypatch.setattr("mihomo_py.config.urllib.request.build_opener", lambda *args: Opener())
    with pytest.raises(AppError) as error:
        fetch("https://example.invalid/private-token", progress=progress.append)
    assert error.value.kind == "download_timeout"
    assert len(calls) == 2
    assert len(progress) == 1 and "重试一次" in progress[0]
    assert "private" not in str(error.value)


def test_retry_can_download_real_http_subscription(http_source, monkeypatch):
    import urllib.request

    real_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    calls = []

    class Opener:
        def open(self, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise TimeoutError
            return real_opener.open(*args, **kwargs)

    monkeypatch.setattr("mihomo_py.config.urllib.request.build_opener", lambda *args: Opener())
    assert "MATCH,DIRECT" in fetch(http_source["url"])
    assert len(calls) == 2
    assert len(http_source["requests"]) == 1


def test_validation_timeout_retains_diagnostics_without_saving(tmp_path, source, monkeypatch):
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setenv("MIHOMO_PY_GEODATA_DIR", str(assets))
    binary = tmp_path / "fake-core"
    binary.write_text(
        f"#!{sys.executable}\nimport time\n"
        'print("Can\'t find MMDB, start download PRIVATE-TOKEN", flush=True)\n'
        "time.sleep(10)\n"
    )
    binary.chmod(0o700)
    manager = Manager(tmp_path / "state", str(binary), timeout=0.3)
    progress = []
    before = manager.store.read()
    with pytest.raises(AppError) as error:
        manager.put_sub("test", str(source), create=True, progress=progress.append)
    assert error.value.kind == "validation_timeout"
    assert "订阅已读取" in str(error.value)
    assert "尚未保存" in str(error.value)
    assert "PRIVATE-TOKEN" not in str(error.value)
    log = manager.store.root / "validation.log"
    assert "PRIVATE-TOKEN" in log.read_text()
    assert log.stat().st_mode & 0o777 == 0o600
    assert manager.store.read() == before
    assert not list(manager.store.root.glob(".check-*"))
    assert "正在读取" in progress[0]
    assert "正在校验" in progress[-1]
