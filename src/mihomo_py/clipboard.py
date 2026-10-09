"""Clipboard transport, following dt view's terminal and local channels."""

import base64
import os
import shutil
import subprocess


def osc52(text):
    payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
    sequence = f"\x1b]52;c;{payload}\a"
    if os.environ.get("TMUX"):
        return "\x1bPtmux;" + sequence.replace("\x1b", "\x1b\x1b") + "\x1b\\"
    if os.environ.get("STY"):
        return "\x1bP" + sequence + "\x1b\\"
    return sequence


def _pipe(command, text):
    if not shutil.which(command[0]):
        return False
    try:
        subprocess.run(
            command,
            input=text.encode("utf-8"),
            timeout=0.5,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def copy_external(text):
    """Try available desktop tools and tmux without requiring a desktop session."""
    commands = []
    if os.environ.get("WAYLAND_DISPLAY"):
        commands.append(["wl-copy"])
    if os.environ.get("DISPLAY"):
        commands.extend((["xclip", "-selection", "clipboard"], ["xsel", "-ib"]))
    for command in commands:
        if _pipe(command, text):
            break
    if os.environ.get("TMUX"):
        _pipe(["tmux", "load-buffer", "-w", "-"], text)
