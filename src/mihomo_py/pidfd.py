"""pidfd operations without depending on how the Python interpreter was built."""

import ctypes
import errno
import os
import platform
import signal
import sys
from functools import lru_cache


@lru_cache(maxsize=1)
def _syscall():
    # These numbers are shared by Linux x86_64 and AArch64 LP64 ABIs.
    # Sources: Linux arch/x86/entry/syscalls/syscall_64.tbl and
    # include/uapi/asm-generic/unistd.h (v6.12). Do not guess on other ABIs.
    if (
        sys.platform != "linux"
        or platform.machine() not in ("x86_64", "aarch64")
        or ctypes.sizeof(ctypes.c_long) != 8
        or ctypes.sizeof(ctypes.c_void_p) != 8
    ):
        raise OSError(errno.ENOSYS, "No pidfd fallback for this platform ABI")
    library = ctypes.CDLL(None, use_errno=True)
    try:
        call = library.syscall
    except AttributeError as exc:
        raise OSError(errno.ENOSYS, "libc does not expose syscall") from exc
    call.restype = ctypes.c_long
    call.argtypes = [ctypes.c_long]  # Remaining syscall arguments are variadic.
    return call


def _call(number, *args):
    result = _syscall()(number, *args)
    if result == -1:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def open_pidfd(pid):
    native = getattr(os, "pidfd_open", None)
    if native is not None:
        return native(pid, 0)
    return _call(434, ctypes.c_int(pid), ctypes.c_uint(0))


def send_signal(fd, sig):
    native = getattr(signal, "pidfd_send_signal", None)
    if native is not None:
        native(fd, sig, None, 0)
    else:
        _call(424, ctypes.c_int(fd), ctypes.c_int(sig), ctypes.c_void_p(), ctypes.c_uint(0))
