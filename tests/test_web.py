import http.client
import json
import socket
from pathlib import Path

import pytest
import yaml

from mihomo_py.bundle import dashboard_root, seed_dashboard
from mihomo_py.controller import Controller
from mihomo_py.errors import AppError


def request(host, port, path, secret=None):
    connection = http.client.HTTPConnection(host, port, timeout=3)
    try:
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_default_and_legacy_settings_use_public_controller(command, tmp_path):
    assert json.loads(command("config", "show").stdout)["controller_host"] == "0.0.0.0"
    command("config", "set", "--host", "0.0.0.0")
    path = tmp_path / "home/state.json"
    state = json.loads(path.read_text())
    del state["settings"]["controller_host"]
    path.write_text(json.dumps(state))
    settings = json.loads(command("config", "show").stdout)
    assert settings["host"] == "0.0.0.0"
    assert settings["controller_host"] == "0.0.0.0"
    assert "controller_host" not in json.loads(path.read_text())["settings"]


def test_dashboard_seeding_is_local_and_keeps_package_immutable(tmp_path):
    root = dashboard_root()
    original = (root / "zashboard.zip").read_bytes()
    name = seed_dashboard(tmp_path)
    index = tmp_path / name / "index.html"
    assert "zashboard" in index.read_text()
    assert "Content-Security-Policy" in index.read_text()
    assert (tmp_path / name / "mihomo-py.js").is_file()
    assert (tmp_path / name / "THIRD_PARTY_NOTICES.md").is_file()
    assert seed_dashboard(tmp_path) == name
    assert (root / "zashboard.zip").read_bytes() == original
    assert not list(tmp_path.glob(".ui-*"))


@pytest.mark.integration
@pytest.mark.parametrize("host", ["0.0.0.0", "127.0.0.2"])
def test_real_dashboard_bind_authentication_and_stable_secret(real_core, command, source, host):
    real_core.put_sub("a", str(source), create=True)
    real_core.use("a")
    before = real_core.start()
    secret = real_core.web()["secret"]
    # Existing 0.1.1 process records must still be recognizable during upgrade.
    record = json.loads(real_core.engine.record_path.read_text())
    del record["settings"]["controller_host"]
    real_core.engine.record_path.write_text(json.dumps(record))
    assert real_core.status()["pid"] == before["pid"]
    assert real_core.status()["healthy"]
    command("config", "set", "--controller-host", host)
    status = real_core.status()
    assert status["healthy"] and status["running_settings"]["controller_host"] == host
    assert status["settings"]["host"] == "127.0.0.1"  # Independent proxy bind.
    port = status["settings"]["controller_port"]
    info = json.loads(command("core", "web").stdout)
    assert info["listen_host"] == host and info["secret"] == secret
    assert "secret" not in command("core", "status").stdout
    for credential in (None, "wrong", secret):
        code, payload = request("127.0.0.2", port, "/version", credential)
        assert code == (200 if credential == secret else 401)
    code, index = request("127.0.0.2", port, "/ui/")
    assert code == 200 and b"zashboard" in index and secret.encode() not in index
    compiled = yaml.safe_load(real_core.engine.runtime_path.read_text())
    ui = Path(real_core.engine.running()["data_dir"]) / compiled["external-ui"]
    for asset in ui.rglob("*"):
        if asset.is_file():
            relative = asset.relative_to(ui).as_posix()
            # Go's FileServer redirects explicit index.html to the directory URL.
            path = "/ui/" if relative == "index.html" else "/ui/" + relative
            code, payload = request("127.0.0.2", port, path)
            assert code == 200 and payload == asset.read_bytes()
    assert Controller(real_core.engine).proxies()["DIRECT"]["type"] == "Direct"
    real_core.start(restart=True)
    assert real_core.web()["secret"] == secret
    real_core.engine.stop()
    real_core.start()
    assert real_core.web()["secret"] == secret
    assert (real_core.store.root / "controller-secret").stat().st_mode & 0o777 == 0o600
    real_core.configure({"controller_host": "127.0.0.1"})
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.2", port), timeout=1)


@pytest.mark.integration
def test_controller_wildcard_conflict_rolls_back(real_core, source):
    real_core.put_sub("a", str(source), create=True)
    real_core.use("a")
    before = real_core.start()
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.2", before["settings"]["controller_port"]))
        occupied.listen()
        with pytest.raises(AppError, match="端口"):
            real_core.configure({"controller_host": "0.0.0.0"})
    after = real_core.status()
    assert after["healthy"]
    assert after["settings"] == after["running_settings"] == before["settings"]


@pytest.mark.integration
def test_subscription_cannot_choose_dashboard_or_controller_bind(real_core, source):
    source.write_text(source.read_text() + """
external-controller: 0.0.0.0:9999
external-ui: /tmp/untrusted-ui
external-ui-url: https://github.com/untrusted/ui.zip
external-ui-name: untrusted
secret: untrusted
""")
    real_core.put_sub("a", str(source), create=True)
    real_core.use("a")
    status = real_core.start()
    compiled = yaml.safe_load(real_core.engine.runtime_path.read_text())
    assert compiled["external-controller"] == f"127.0.0.1:{status['settings']['controller_port']}"
    assert compiled["external-ui"].startswith("ui-")
    assert "external-ui-name" not in compiled and "external-ui-url" not in compiled
    assert compiled["secret"] != "untrusted"


@pytest.mark.integration
def test_base_client_runs_without_dashboard_resources(real_core, source, monkeypatch):
    monkeypatch.setattr("mihomo_py.bundle.find_spec", lambda name: None)
    real_core.put_sub("a", str(source), create=True)
    real_core.use("a")
    assert real_core.start()["healthy"]
    record = real_core.engine.running()
    compiled = yaml.safe_load(real_core.engine.runtime_path.read_text())
    assert "external-ui" not in compiled
    assert not list(Path(record["data_dir"]).glob("ui-*"))
    code, _ = request(
        "127.0.0.1", record["settings"]["controller_port"], "/ui/", record["secret"]
    )
    assert code == 404
    with pytest.raises(AppError) as exc:
        real_core.web()
    assert exc.value.kind == "web_missing" and "[web]" in exc.value.suggestion


@pytest.mark.integration
def test_extra_installed_after_start_requires_restart(real_core, source, monkeypatch):
    with monkeypatch.context() as missing:
        missing.setattr("mihomo_py.bundle.find_spec", lambda name: None)
        real_core.put_sub("a", str(source), create=True)
        real_core.use("a")
        real_core.start()
    record = real_core.engine.running()
    port = record["settings"]["controller_port"]
    assert request("127.0.0.1", port, "/ui/", record["secret"])[0] == 404
    with pytest.raises(AppError) as exc:
        real_core.web()
    assert exc.value.kind == "web_restart_required"
    assert "core restart" in exc.value.suggestion
    real_core.start(restart=True)
    assert real_core.web()["secret"] == record["secret"]
    assert request("127.0.0.1", port, "/ui/")[0] == 200
