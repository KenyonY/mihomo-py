import base64
import subprocess

import pytest

from mihomo_py import clipboard


@pytest.mark.parametrize("session", [None, "TMUX", "STY"])
def test_osc52_preserves_unicode_and_wraps_terminal_session(monkeypatch, session):
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("STY", raising=False)
    if session:
        monkeypatch.setenv(session, "session")
    text = "中文节点\nabc-123"
    sequence = f"\x1b]52;c;{base64.b64encode(text.encode()).decode()}\a"
    expected = sequence
    if session == "TMUX":
        expected = "\x1bPtmux;" + sequence.replace("\x1b", "\x1b\x1b") + "\x1b\\"
    elif session == "STY":
        expected = "\x1bP" + sequence + "\x1b\\"
    assert clipboard.osc52(text) == expected


def test_external_copy_falls_back_and_also_populates_tmux(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-test")
    monkeypatch.setenv("DISPLAY", ":test")
    monkeypatch.setenv("TMUX", "session")
    calls = []

    def pipe(command, text):
        calls.append((command, text))
        return command[0] == "xclip"

    monkeypatch.setattr(clipboard, "_pipe", pipe)
    clipboard.copy_external("文本")
    assert calls == [
        (["wl-copy"], "文本"),
        (["xclip", "-selection", "clipboard"], "文本"),
        (["tmux", "load-buffer", "-w", "-"], "文本"),
    ]


def test_external_copy_without_desktop_or_tmux_uses_no_tools(monkeypatch):
    for variable in ("WAYLAND_DISPLAY", "DISPLAY", "TMUX"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(clipboard, "_pipe", lambda *_: pytest.fail("No clipboard tool needed"))
    clipboard.copy_external("文本")


@pytest.mark.parametrize("error", [
    OSError("unavailable"),
    subprocess.CalledProcessError(1, "copy"),
    subprocess.TimeoutExpired("copy", 0.5),
])
def test_clipboard_tool_failure_is_bounded_and_nonfatal(monkeypatch, error):
    monkeypatch.setattr(clipboard.shutil, "which", lambda _: "/bin/copy")

    def run(command, **kwargs):
        assert kwargs["timeout"] == 0.5
        assert kwargs["input"] == "中文".encode()
        raise error

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert not clipboard._pipe(["copy"], "中文")


def test_missing_clipboard_tool_is_nonfatal(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda _: None)
    assert not clipboard._pipe(["missing"], "text")
