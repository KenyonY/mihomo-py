"""Exercise the real terminal driver, including input and modal routing."""

import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time

import pytest


@pytest.mark.parametrize("no_color", [False, True])
def test_terminal_navigation_form_error_and_exit(tmp_path, no_color):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    env = {**os.environ, "TERM": "xterm-256color"}
    env.pop("NO_COLOR", None)
    if no_color:
        env["NO_COLOR"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "mihomo_py", "--data-dir", str(tmp_path / "home"), "tui"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        start_new_session=True,
    )
    os.close(slave)

    def wait_text(expected):
        output = b""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    output += os.read(master, 65536)
                except OSError:
                    break
                if expected.encode() in output:
                    return
            if process.poll() is not None:
                break
        pytest.fail(f"Missing {expected!r}: {output.decode(errors='replace')[-4000:]}")

    def send(data):
        os.write(master, data)
        # Allow the terminal driver to finish each navigation before typing into its target.
        time.sleep(0.15)

    try:
        wait_text("退出界面")
        send(b"2")
        wait_text("内核已停止")
        send(b"/")
        wait_text("搜索节点名称")
        send(b"123?iq")
        wait_text("123?iq")
        assert process.poll() is None
        send(b"\x1b")
        wait_text("搜索节点名称")
        send(b"?")
        wait_text("键盘操作")
        send(b"\x1b[<0;1;1M\x1b[<0;1;1m")  # Left-click the backdrop.
        wait_text("内核已停止")
        send(b"1")
        wait_text("还没有订阅")
        send(b"\x01")
        wait_text("订阅名称")
        send(b"work")
        send(b"\t")
        send(str(tmp_path / "missing.yaml").encode())
        send(b"\r")
        wait_text("无法读取")
        send(b"\x1b[<0;1;1M\x1b[<0;1;1m")
        wait_text("还没有订阅")
        send(b"\x11")
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        os.close(master)
