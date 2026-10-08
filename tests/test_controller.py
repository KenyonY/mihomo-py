import fcntl
import json
import os
import pty
import select
import socket
import struct
import subprocess
import sys
import termios
import time

import pytest

from mihomo_py.controller import Controller, instance_id
from mihomo_py.errors import AppError


@pytest.fixture
def grouped_core(real_core, source):
    source.write_text("""proxies: []
proxy-groups:
  - name: "代理 / [组]"
    type: select
    proxies: [DIRECT, REJECT]
rules:
  - "MATCH,代理 / [组]"
""")
    real_core.put_sub("grouped", str(source), create=True)
    real_core.use("grouped")
    real_core.start()
    return real_core


@pytest.mark.integration
def test_real_node_cli_selection_persists(grouped_core, command):
    groups = json.loads(command("node", "list").stdout)
    assert any(group["name"] == "代理 / [组]" for group in groups)
    nodes = json.loads(command("node", "list", "--group", "代理 / [组]").stdout)
    assert [node["name"] for node in nodes] == ["DIRECT", "REJECT"]
    before = grouped_core.status()["pid"]
    result = command("node", "use", "REJECT", "--group", "代理 / [组]")
    assert json.loads(result.stdout)["selected"] == "REJECT"
    assert grouped_core.status()["pid"] == before
    grouped_core.start(restart=True)
    assert Controller(grouped_core.engine).proxies()["代理 / [组]"]["now"] == "REJECT"
    command("node", "use", "DIRECT", "--group", "代理 / [组]")


@pytest.mark.integration
def test_real_delay_and_failure(grouped_core, command, http_source):
    result = json.loads(command("node", "test", "DIRECT", "--url", http_source["url"]).stdout)
    assert result["delay_ms"] >= 0
    assert http_source["requests"]
    result = command("node", "test", "DIRECT", "--url", "/tmp/not-a-url", expected=2)
    assert json.loads(result.stderr)["error"] == "invalid_url"
    # A bound non-listening socket guarantees a local connection failure without external egress.
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{closed.getsockname()[1]}/"
        command("node", "test", "DIRECT", "--url", url, "--timeout-ms", "100", expected=1)


@pytest.mark.integration
def test_stale_instance_and_unknown_member(grouped_core, command):
    previous = instance_id(grouped_core.engine.running())
    controller = Controller(grouped_core.engine)
    grouped_core.start(restart=True)
    with pytest.raises(AppError, match="实例已变化"):
        Controller(grouped_core.engine, previous)
    with pytest.raises(AppError, match="实例已变化"):
        controller.proxies()
    command("node", "use", "missing", "--group", "代理 / [组]", expected=3)
    command("node", "list", "--group", "missing", expected=3)


def test_noninteractive_default_and_explicit_tui(command, tmp_path):
    assert json.loads(command().stdout)["running"] is False
    assert json.loads(command("--json").stdout)["running"] is False
    result = command("tui", expected=2)
    assert json.loads(result.stderr)["error"] == "terminal_required"
    assert not (tmp_path / "home").exists()


def test_node_dry_run_without_core(command, tmp_path):
    command("node", "use", "DIRECT", "--group", "test", "--dry-run", expected=10)
    command("node", "test", "DIRECT", "--dry-run", expected=10)
    command("node", "list", expected=3)
    assert not (tmp_path / "home").exists()


@pytest.mark.parametrize("arguments", [[], ["tui"]])
def test_actual_terminal_entry_and_exit(tmp_path, arguments):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    process = subprocess.Popen(
        [sys.executable, "-m", "mihomo_py", "--data-dir", str(tmp_path / "home"), *arguments],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env={**os.environ, "TERM": "xterm-256color"},
        start_new_session=True,
    )
    os.close(slave)
    output = b""
    quit_sent = False
    deadline = time.monotonic() + 15
    try:
        while process.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    output += os.read(master, 65536)
                except OSError:
                    break
            if not quit_sent and "退出界面".encode() in output:
                os.write(master, b"q")
                quit_sent = True
        assert quit_sent, output.decode(errors="replace")[-3000:]
        assert process.wait(timeout=3) == 0, output.decode(errors="replace")[-3000:]
        assert not (tmp_path / "home").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        os.close(master)
