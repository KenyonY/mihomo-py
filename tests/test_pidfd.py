import errno
import os
import select
import signal
import subprocess
import sys

import pytest

from mihomo_py import pidfd
from mihomo_py.engine import Engine
from mihomo_py.errors import AppError


@pytest.fixture
def fallback(monkeypatch):
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    monkeypatch.delattr(signal, "pidfd_send_signal", raising=False)
    pidfd._syscall.cache_clear()
    yield
    pidfd._syscall.cache_clear()


def test_fallback_signals_and_polls_a_real_process(fallback):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    descriptor = None
    try:
        descriptor = pidfd.open_pidfd(process.pid)
        assert not os.get_inheritable(descriptor)
        pidfd.send_signal(descriptor, 0)
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)
        assert not poller.poll(0)
        pidfd.send_signal(descriptor, signal.SIGTERM)
        assert poller.poll(3000)
        assert process.wait(timeout=3) == -signal.SIGTERM
        with pytest.raises(ProcessLookupError):
            pidfd.send_signal(descriptor, 0)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)


def test_fallback_preserves_errno(fallback):
    with pytest.raises(OSError) as error:
        pidfd.send_signal(-1, 0)
    assert error.value.errno == errno.EBADF


def test_unknown_abi_rejected_without_a_syscall(fallback, monkeypatch):
    monkeypatch.setattr(pidfd.platform, "machine", lambda: "unknown-abi")
    with pytest.raises(OSError) as error:
        pidfd.open_pidfd(os.getpid())
    assert error.value.errno == errno.ENOSYS


def test_probe_closes_descriptor_when_signals_are_blocked(monkeypatch):
    descriptors = []
    original = pidfd.open_pidfd

    def opened(pid):
        fd = original(pid)
        descriptors.append(fd)
        return fd

    def blocked(fd, sig):
        raise PermissionError(errno.EPERM, "blocked")

    monkeypatch.setattr(pidfd, "open_pidfd", opened)
    monkeypatch.setattr(pidfd, "send_signal", blocked)
    with pytest.raises(AppError, match="errno=1"):
        Engine.require_process_api()
    with pytest.raises(OSError) as error:
        os.fstat(descriptors[0])
    assert error.value.errno == errno.EBADF
