import json
import os
import subprocess
import sys

import pytest

from mihomo_py.config import fetch, normalize_source, render
from mihomo_py.errors import AppError
from mihomo_py.store import Store


def test_discovery_and_read_only_commands(command, tmp_path):
    assert "sub" in command("--help").stdout
    assert "0.1.0" in command("--version").stdout
    assert json.loads(command("sub", "list").stdout) == []
    assert json.loads(command("core", "status").stdout)["running"] is False
    assert json.loads(command("config", "show").stdout)["proxy_port"] == 7897
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize(
    "args",
    [
        ["core", "start", "--dry-run"],
        ["core", "restart", "--dry-run"],
        ["core", "stop", "--dry-run"],
        ["sub", "use", "test", "--dry-run"],
        ["sub", "remove", "test", "--dry-run"],
        ["sub", "add", "test", "https://example.com/token-secret", "--dry-run"],
        ["sub", "set", "test", "https://example.com/token-secret", "--dry-run"],
        ["config", "set", "--mode", "direct", "--dry-run"],
    ],
)
def test_dry_run_has_no_side_effect(command, tmp_path, args):
    result = command(*args, expected=10)
    assert json.loads(result.stdout)["executed"] is False
    assert "token-secret" not in result.stdout
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize(
    "args,kind,code",
    [
        (["unknown"], "usage_error", 2),
        (["sub", "remove", "test"], "confirmation_required", 2),
        (["core", "start"], "not_found", 3),
        (["config", "set"], "usage_error", 2),
        (["config", "set", "--proxy-port", "0"], "usage_error", 2),
        (["config", "set", "--proxy-port", "9090"], "invalid_ports", 2),
        (["sub", "add", "../bad", "file.yaml"], "invalid_name", 2),
        (["sub", "add", "test", "file:///etc/passwd"], "invalid_source", 2),
    ],
)
def test_errors_are_structured(command, args, kind, code):
    result = command(*args, expected=code)
    assert not result.stdout
    assert json.loads(result.stderr)["error"] == kind


def test_state_lock_and_permissions(tmp_path):
    store = Store(tmp_path / "home")
    with store.lock():
        store.save(store.read())
        with pytest.raises(AppError, match="另一个命令"):
            with Store(store.root).lock():
                pass
    assert store.root.stat().st_mode & 0o777 == 0o700
    assert (store.root / "state.json").stat().st_mode & 0o777 == 0o600


def test_invalid_state_reports_actionable_error(command, tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    (root / "state.json").write_text('{"version": 99}')
    result = command("sub", "list", expected=1)
    assert json.loads(result.stderr)["error"] == "invalid_state"


def test_source_fetch_bypasses_proxy_and_redacts_errors(http_source, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", "")
    assert "MATCH,DIRECT" in fetch(http_source["url"])
    assert http_source["requests"][-1][1] == "Clash.Meta/mihomo-py"
    http_source["status"] = 403
    with pytest.raises(AppError) as error:
        fetch(http_source["url"])
    assert "PRIVATE-TOKEN" not in str(error.value)
    assert error.value.retryable is False


@pytest.mark.parametrize(
    "value",
    ["https://user:secret@example.com/x", "ftp://example.com/x", "https://example.com:bad/x"],
)
def test_bad_url(value):
    with pytest.raises(AppError):
        normalize_source(value)


def test_no_core_clear_error(command, source):
    result = command(
        "--core-binary", "nonexistent-mihomo-for-test", "sub", "add", "a", str(source), expected=3
    )
    assert json.loads(result.stderr)["error"] == "core_missing"


def test_render_preserves_source_and_controls_inbound_settings():
    import yaml

    source = """proxies: []
rules: ['MATCH,DIRECT']
mixed-port: 1234
allow-lan: true
external-controller: 0.0.0.0:8080
external-controller-unix: /tmp/shared.sock
external-doh-server: /dns-query
iptables: {enable: true, dns-redirect: true}
ntp: {enable: true, write-to-system: true}
listeners: [{name: other, type: mixed, port: 8888}]
ss-config: ss://aes-128-gcm:secret@:8889
vmess-config: vmess://1:00000000-0000-0000-0000-000000000000@:8890
tuic-server: {enable: true, listen: '0.0.0.0:8891'}
tun: {enable: true}
dns: {enable: true, listen: '0.0.0.0:53', nameserver: [1.1.1.1]}
"""
    compiled = yaml.safe_load(
        render(source, {"proxy_port": 12345, "controller_port": 12346, "mode": "rule"}, "secret")
    )
    assert compiled["mixed-port"] == 12345
    assert compiled["tun"]["enable"] is False
    assert compiled["allow-lan"] is False
    assert compiled["secret"] == "secret"
    assert "listen" not in compiled["dns"]
    assert "listeners" not in compiled
    assert all(key not in compiled for key in ("ss-config", "vmess-config", "tuic-server"))
    assert all(key not in compiled for key in ("external-doh-server", "iptables", "ntp"))
    assert "external-controller-unix" not in compiled
    assert "mixed-port: 1234" in source


def test_foreign_pid_is_never_stopped(tmp_path):
    from mihomo_py.engine import Engine, process_identity

    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        engine = Engine(tmp_path)
        record = {
            "pid": process.pid,
            "identity": process_identity(process.pid),
            "data_dir": str(tmp_path),
            "subscription": "other",
            "fingerprint": "unused",
            "secret": "unused",
            "settings": {"proxy_port": 7897, "controller_port": 9090, "mode": "rule"},
        }
        engine.record_path.write_text(json.dumps(record))
        assert engine.stop() is False
        assert process.poll() is None
        record["identity"]["start_ticks"] = "0"
        engine.record_path.write_text(json.dumps(record))
        assert engine.stop() is False
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait()


def test_logs_follow_json(command, tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    (root / "core.log").write_text("first\nsecond\n")
    assert json.loads(command("core", "logs", "--lines", "1").stdout) == {"lines": ["second"]}
    process = subprocess.Popen(
        [sys.executable, "-m", "mihomo_py", "--data-dir", str(root), "core", "logs", "--follow"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        import select

        ready, _, _ = select.select([process.stdout], [], [], 5)
        assert ready
        assert json.loads(process.stdout.readline()) == {"line": "first"}
    finally:
        process.send_signal(2)
        process.communicate(timeout=5)
    assert process.returncode == 130


def test_stdin_source_dryrun(command):
    result = command(
        "sub",
        "add",
        "secret",
        "-",
        "--dry-run",
        input="https://example.com/private?token=SECRET\n",
        expected=10,
    )
    assert "SECRET" not in result.stdout
    assert json.loads(result.stdout)["target"]["source"] == "https://example.com/…"


@pytest.mark.parametrize("scheme", ["HTTPS", "hTtPs"])
def test_url_scheme_case_does_not_expose_token(command, scheme):
    result = command(
        "sub", "add", "a", f"{scheme}://example.com/sub?token=SECRET", "--dry-run", expected=10
    )
    assert "SECRET" not in result.stdout
    assert json.loads(result.stdout)["target"]["source"] == "https://example.com/…"


def test_uppercase_http_fetch(http_source):
    source = normalize_source(http_source["url"].replace("http://", "HTTP://"))
    assert "MATCH,DIRECT" in fetch(source)
    assert http_source["requests"]


def test_xdg_default_read_only(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "mihomo_py", "core", "status"],
        env={**os.environ, "XDG_CONFIG_HOME": str(tmp_path), "MIHOMO_PY_HOME": ""},
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["data_dir"] == str(tmp_path / "mihomo-py")
    assert not (tmp_path / "mihomo-py").exists()


@pytest.mark.parametrize("xdg", ["", "relative-config"])
def test_empty_or_relative_xdg_uses_home(command, tmp_path, xdg):
    home = tmp_path / "user-home"
    result = subprocess.run(
        [sys.executable, "-m", "mihomo_py", "core", "status"],
        env={**os.environ, "HOME": str(home), "XDG_CONFIG_HOME": xdg, "MIHOMO_PY_HOME": ""},
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["data_dir"] == str(home / ".config" / "mihomo-py")
    assert not home.exists()


def test_unsupported_python_is_rejected_before_start(tmp_path, monkeypatch):
    from mihomo_py.engine import Engine

    monkeypatch.delattr(os, "pidfd_open")
    with pytest.raises(AppError, match="pidfd"):
        Engine(tmp_path).executable()
    assert not list(tmp_path.iterdir())


def test_atomic_write_without_directory_fsync(tmp_path, monkeypatch):
    import stat

    from mihomo_py.store import atomic_write

    original = os.fsync

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("Directory fsync unsupported")
        return original(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    target = tmp_path / "state"
    atomic_write(target, "old")
    atomic_write(target, "new")
    assert target.read_text() == "new"
    assert not list(tmp_path.glob(".tmp-*"))


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_atomic_write_failure_preserves_previous_file(tmp_path, monkeypatch, operation):
    from mihomo_py.store import atomic_write

    target = tmp_path / "state"
    atomic_write(target, "old")

    def fail(*args):
        raise OSError("Injected disk failure")

    monkeypatch.setattr(os, operation, fail)
    with pytest.raises(OSError):
        atomic_write(target, "new")
    assert target.read_text() == "old"
    assert not list(tmp_path.glob(".tmp-*"))
