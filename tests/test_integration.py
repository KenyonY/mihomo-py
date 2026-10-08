import http.client
import json
import socket
from pathlib import Path

import pytest
import yaml

from mihomo_py.errors import AppError

pytestmark = pytest.mark.integration


def test_real_cli_lifecycle(real_core, command, source, http_source):
    command("sub", "add", "a", str(source))
    command("sub", "add", "a", str(source), expected=5)
    command("sub", "use", "a")
    started = json.loads(command("core", "start").stdout)
    assert started["running"] and started["healthy"]
    assert json.loads(command("core", "start").stdout)["pid"] == started["pid"]
    assert source.read_text() == "proxies: []\nrules:\n  - MATCH,DIRECT\n"

    # A real HTTP request through the real mihomo proxy to a local HTTP origin.
    proxy = http.client.HTTPConnection("127.0.0.1", started["settings"]["proxy_port"], timeout=3)
    proxy.request("GET", http_source["url"])
    response = proxy.getresponse()
    assert response.status == 200
    assert b"MATCH,DIRECT" in response.read()
    proxy.close()

    state_before = (real_core.store.root / "state.json").read_bytes()
    source.write_text("<html>not a subscription</html>")
    command("sub", "update", expected=2)
    assert (real_core.store.root / "state.json").read_bytes() == state_before
    assert json.loads(command("core", "status").stdout)["pid"] == started["pid"]

    # The cached config still starts without re-reading the now-invalid source.
    restarted = json.loads(command("core", "restart").stdout)
    assert restarted["healthy"] and restarted["pid"] != started["pid"]
    command("sub", "remove", "a", "--yes", expected=5)
    assert json.loads(command("core", "logs", "--lines", "2").stdout)["lines"]
    command("core", "stop")
    assert json.loads(command("core", "stop").stdout) == {"stopped": False}
    command("sub", "remove", "a", "--yes")
    assert json.loads(command("sub", "list").stdout) == []
    assert json.loads(command("core", "status").stdout)["selected"] is None


def test_live_update_switch_and_settings(real_core, command, source, http_source):
    command("sub", "add", "remote", "-", input=http_source["url"] + "\n")
    result = command("sub", "list")
    assert "PRIVATE-TOKEN" not in result.stdout
    command("sub", "use", "remote")
    command("core", "start")
    http_source["content"] = b"proxies: []\nrules: ['MATCH,DIRECT']\nlog-level: warning\n"
    command("sub", "update")
    assert yaml.safe_load(real_core.engine.runtime_path.read_text())["log-level"] == "warning"
    command("sub", "add", "local", str(source))
    command("sub", "use", "local")
    switched = json.loads(command("core", "status").stdout)
    assert switched["selected"] == switched["running_subscription"] == "local"
    command("config", "set", "--mode", "direct")
    status = json.loads(command("core", "status").stdout)
    assert status["running_settings"]["mode"] == "direct"
    assert status["healthy"]
    source.write_text("proxies: []\nrules: ['MATCH,DIRECT']\nmode: global\n")
    command("sub", "update")
    assert yaml.safe_load(real_core.engine.runtime_path.read_text())["mode"] == "direct"
    assert real_core.store.read()["settings"]["mode"] == "direct"


def test_real_validation_rejects_invalid_proxy_without_changing_cache(real_core, command, source):
    command("sub", "add", "a", str(source))
    before = real_core.store.read()
    source.write_text("proxies: [{name: bad, type: made-up-protocol}]\nrules: ['MATCH,DIRECT']\n")
    result = command("sub", "set", "a", str(source), expected=2)
    assert json.loads(result.stderr)["error"] == "validation_failed"
    assert real_core.store.read() == before
    assert (real_core.store.root / "validation.log").exists()
    assert not list(real_core.store.root.glob(".check-*"))


@pytest.mark.parametrize("kind", [socket.SOCK_STREAM, socket.SOCK_DGRAM])
def test_port_conflict_keeps_running_instance(real_core, command, source, kind):
    command("sub", "add", "a", str(source))
    command("sub", "use", "a")
    started = json.loads(command("core", "start").stdout)
    with socket.socket(type=kind) as occupied:
        occupied.bind(("127.0.0.1", 0))
        if kind == socket.SOCK_STREAM:
            occupied.listen()
        result = command(
            "config", "set", "--proxy-port", str(occupied.getsockname()[1]), expected=5
        )
        assert json.loads(result.stderr)["error"] == "port_in_use"
    status = json.loads(command("core", "status").stdout)
    assert status["pid"] == started["pid"]
    assert status["settings"] == started["settings"]
    assert status["healthy"]


def test_failed_start_restores_real_previous_process(real_core, source, monkeypatch):
    real_core.put_sub("a", str(source), create=True)
    real_core.use("a")
    before = real_core.start()
    original = real_core.engine.launch
    calls = []

    def fail_once(*args):
        calls.append(args)
        if len(calls) == 1:
            raise AppError("start_failed", "Injected launch failure")
        return original(*args)

    monkeypatch.setattr(real_core.engine, "launch", fail_once)
    with pytest.raises(AppError, match="Injected"):
        real_core.configure({"mode": "direct"})
    status = real_core.status()
    assert len(calls) == 2
    assert status["settings"] == before["settings"]
    assert status["running_settings"] == before["running_settings"]
    assert status["healthy"]


def test_stale_identity_does_not_stop_real_core(real_core, source):
    real_core.put_sub("a", str(source), create=True)
    real_core.use("a")
    started = real_core.start()
    saved = real_core.engine.record_path.read_text()
    record = json.loads(saved)
    record["identity"]["start_ticks"] = "0"
    try:
        real_core.engine.record_path.write_text(json.dumps(record))
        assert real_core.engine.stop() is False
        assert real_core.engine.healthy(json.loads(saved))
    finally:
        real_core.engine.record_path.write_text(saved)
    assert real_core.status()["pid"] == started["pid"]


def test_stopped_selection_and_settings_do_not_require_core(real_core, command, source):
    command("sub", "add", "a", str(source))
    command("sub", "add", "b", str(source))
    unavailable = ("--core-binary", "nonexistent-mihomo-for-test")
    command(*unavailable, "sub", "use", "a")
    command(*unavailable, "config", "set", "--mode", "direct")
    command(*unavailable, "sub", "use", "b")
    status = json.loads(command(*unavailable, "core", "status").stdout)
    assert status["selected"] == "b" and status["settings"]["mode"] == "direct"
    assert status["running"] is False
    command(*unavailable, "core", "start", expected=3)


def test_subscription_cannot_add_inbound_listeners(real_core, source):
    content = yaml.safe_load(source.read_text())
    content.update(
        {
            "ss-config": "ss://aes-128-gcm:secret@:8889",
            "vmess-config": "vmess://1:00000000-0000-0000-0000-000000000000@:8890",
            "tuic-server": {"enable": True, "listen": "0.0.0.0:8891"},
            "listeners": [{"name": "extra", "type": "mixed", "port": 8892}],
            "external-doh-server": "/dns-query",
            "iptables": {"enable": True, "dns-redirect": True},
            "ntp": {"enable": True, "write-to-system": True},
        }
    )
    source.write_text(yaml.safe_dump(content))
    real_core.put_sub("a", str(source), create=True)
    real_core.use("a")
    status = real_core.start()
    compiled = yaml.safe_load(real_core.engine.runtime_path.read_text())
    assert all(key not in compiled for key in ("external-doh-server", "iptables", "ntp"))
    authorization = {"Authorization": f"Bearer {compiled['secret']}"}
    for path, headers, expected in (
        ("/dns-query", {}, 404),
        ("/dns-query", authorization, 404),
        ("/version", {}, 401),
        ("/version", authorization, 200),
    ):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            status["settings"]["controller_port"],
            timeout=3,
        )
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            assert response.status == expected
            response.read()
        finally:
            connection.close()
    proc = Path(f"/proc/{status['pid']}")
    inodes = set()
    for entry in (proc / "fd").iterdir():
        try:
            target = str(entry.readlink())
        except FileNotFoundError:
            continue
        if target.startswith("socket:["):
            inodes.add(target[8:-1])
    tcp_ports = set()
    for table in ("tcp", "tcp6", "udp", "udp6"):
        for line in (proc / "net" / table).read_text().splitlines()[1:]:
            row = line.split()
            if row[9] not in inodes:
                continue
            address, hex_port = row[1].split(":")
            port = int(hex_port, 16)
            if table.startswith("tcp") and row[3] == "0A":
                tcp_ports.add(port)
                assert address != "0" * len(address)
            if table.startswith("udp"):
                assert port == status["settings"]["proxy_port"]
                assert address != "0" * len(address)
    assert tcp_ports == {status["settings"]["proxy_port"], status["settings"]["controller_port"]}


def test_state_save_failure_restores_running_previous_process(real_core, source, monkeypatch):
    real_core.put_sub("a", str(source), create=True)
    real_core.use("a")
    before = real_core.start()

    def failed_save(state):
        raise OSError("Injected state write failure")

    monkeypatch.setattr(real_core.store, "save", failed_save)
    with pytest.raises(OSError, match="Injected state"):
        real_core.configure({"mode": "direct"})
    restored = real_core.status()
    assert restored["healthy"]
    assert restored["running_settings"] == restored["settings"] == before["settings"]


def test_start_waits_for_complete_process_identity(real_core, source, monkeypatch):
    import mihomo_py.engine as engine_module

    original = engine_module.process_identity
    first = True

    def transient(pid):
        nonlocal first
        identity = original(pid)
        if first and identity:
            first = False
            return {**identity, "cmdline": []}
        return identity

    real_core.put_sub("test", str(source), create=True)
    real_core.use("test")
    monkeypatch.setattr(engine_module, "process_identity", transient)
    status = real_core.start()
    assert status["running"] and status["healthy"]
    assert real_core.engine.stop()
