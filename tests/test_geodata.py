import sys
import urllib.error

import pytest

from mihomo_py.config import fetch
from mihomo_py.errors import AppError
from mihomo_py.geodata import seed_geodata
from mihomo_py.manager import Manager


def test_seed_geodata_copies_only_known_assets_and_preserves_existing(
    tmp_path, monkeypatch, mmdb_bytes
):
    source = tmp_path / "assets"
    source.mkdir()
    monkeypatch.delenv("MIHOMO_PY_GEODATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    source.rename(tmp_path / "geodata")
    source = tmp_path / "geodata"
    for name in ("GeoSite.dat", "Country.mmdb", "GeoIP.dat", "ASN.mmdb", "config.yaml", "cache.db"):
        (source / name).write_bytes(mmdb_bytes)
    target = tmp_path / "target"
    target.mkdir()
    (target / "geoip.metadb").write_bytes(mmdb_bytes)
    copied = seed_geodata(tmp_path, target, {"geox-url": {"geosite": "https://custom.invalid"}})
    assert set(copied) == {"GeoIP.dat", "ASN.mmdb"}
    assert (target / "geoip.metadb").read_bytes() == mmdb_bytes
    assert not (target / "Country.mmdb").exists()
    assert not (target / "GeoSite.dat").exists()
    assert not (target / "cache.db").exists()
    assert not (target / "config.yaml").exists()
    assert (target / "ASN.mmdb").stat().st_mode & 0o777 == 0o600
    original_asn = (source / "ASN.mmdb").read_bytes()
    (target / "ASN.mmdb").write_bytes(b"changed")
    assert (source / "ASN.mmdb").read_bytes() == original_asn
    assert not list(target.glob(".geo-*"))


def test_seed_default_directory_priority_and_case_aliases(tmp_path, monkeypatch, mmdb_bytes):
    monkeypatch.delenv("MIHOMO_PY_GEODATA_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    root = tmp_path / "client"
    shared = root / "geodata"
    shared.mkdir(parents=True)
    (shared / "geoip.metadb").write_bytes(mmdb_bytes)
    core_home = tmp_path / ".config" / "mihomo"
    core_home.mkdir(parents=True)
    (core_home / "Country.mmdb").write_bytes(mmdb_bytes)
    (core_home / "GEOSITE.DAT").write_bytes(b"site dataset")
    target = root / "target"
    target.mkdir()
    seed_geodata(root, target, {})
    assert (target / "geoip.metadb").read_bytes() == mmdb_bytes
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


def test_corrupt_target_and_source_are_replaced(real_core, tmp_path, monkeypatch, mmdb_bytes):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "Country.mmdb").write_bytes(b"broken preferred alias")
    (assets / "geoip.metadb").write_bytes(mmdb_bytes)
    monkeypatch.setenv("MIHOMO_PY_GEODATA_DIR", str(assets))
    target = real_core.engine.data_dir("retry")
    (target / "geoip.metadb").write_bytes(b"previous interrupted download")
    source = tmp_path / "geo.yaml"
    source.write_text(
        "rules: [GEOIP,CN,DIRECT]\n".replace("[GEOIP,CN,DIRECT]", "['GEOIP,CN,DIRECT']")
    )
    real_core.put_sub("retry", str(source), create=True)
    assert (target / "geoip.metadb").read_bytes() == mmdb_bytes
    assert not list(target.glob(".geo-*"))


@pytest.mark.parametrize("failure", ["timeout", "exit"])
def test_failed_validation_restores_geodata(tmp_path, source, monkeypatch, mmdb_bytes, failure):
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setenv("MIHOMO_PY_GEODATA_DIR", str(assets))
    binary = tmp_path / "fake-core"
    binary.write_text(
        f"#!{sys.executable}\nimport sys, time\nfrom pathlib import Path\n"
        "directory = Path(sys.argv[sys.argv.index('-d') + 1])\n"
        "(directory / 'geoip.metadb').write_bytes(b'partial replacement')\n"
        "(directory / 'GeoSite.dat').write_bytes(b'partial new file')\n"
        + ("time.sleep(10)\n" if failure == "timeout" else "sys.exit(1)\n")
    )
    binary.chmod(0o700)
    manager = Manager(tmp_path / "state", str(binary), timeout=0.3)
    directory = manager.engine.data_dir("test")
    (directory / "geoip.metadb").write_bytes(mmdb_bytes)
    with pytest.raises(AppError) as error:
        manager.put_sub("test", str(source), create=True)
    assert error.value.kind == (
        "validation_timeout" if failure == "timeout" else "validation_failed"
    )
    assert (directory / "geoip.metadb").read_bytes() == mmdb_bytes
    assert not (directory / "GeoSite.dat").exists()
    assert not list(directory.glob(".geo-*"))
    assert manager.store.read()["subs"] == {}


def test_custom_geodata_uses_its_own_source_and_directory(
    real_core, tmp_path, monkeypatch, mmdb_bytes, http_source
):
    import yaml

    assets = real_core.store.root / "geodata"
    assets.mkdir()
    (assets / "geoip.metadb").write_bytes(mmdb_bytes)
    monkeypatch.delenv("MIHOMO_PY_GEODATA_DIR", raising=False)
    source = tmp_path / "geo.yaml"
    config = {"rules": ["GEOIP,CN,DIRECT", "MATCH,DIRECT"]}
    source.write_text(yaml.safe_dump(config))
    real_core.put_sub("geo", str(source), create=True)
    real_core.use("geo")
    real_core.start()
    default = real_core.engine.data_dir("geo")
    assert (default / "geoip.metadb").read_bytes() == mmdb_bytes
    http_source["content"] = mmdb_bytes
    directories = {default}
    for suffix in ("&version=1", "&version=2"):
        config["geox-url"] = {"mmdb": http_source["url"] + suffix}
        source.write_text(yaml.safe_dump(config))
        real_core.put_sub("geo", str(source))
        directory = real_core.engine.data_dir("geo", config)
        assert directory not in directories
        directories.add(directory)
        assert real_core.engine.record()["data_dir"] == str(directory)
        assert real_core.status()["healthy"]
        assert http_source["requests"][-1][0].endswith(suffix)
        count = len(http_source["requests"])
        real_core.engine.validate(source.read_text(), real_core.store.read()["settings"], "geo")
        assert len(http_source["requests"]) == count
    before = real_core.engine.record()
    saved = real_core.store.read()
    http_source["status"] = 500
    http_source["content"] = b"server error"
    config["geox-url"]["mmdb"] += "&broken=1"
    source.write_text(yaml.safe_dump(config))
    with pytest.raises(AppError):
        real_core.put_sub("geo", str(source))
    assert real_core.store.read() == saved
    assert real_core.engine.record() == before
    assert real_core.status()["healthy"]
    assert not list(real_core.engine.data_dir("geo", config).glob("*.metadb"))
    config.pop("geox-url")
    source.write_text(yaml.safe_dump(config))
    real_core.put_sub("geo", str(source))
    assert real_core.engine.record()["data_dir"] == str(default)
    assert (default / "geoip.metadb").read_bytes() == mmdb_bytes
