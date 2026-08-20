"""Make a service process exit when the main launcher disappears."""

from __future__ import annotations

import os
import threading


def start_parent_guard() -> None:
    """Watch ``NANAON_PARENT_PID`` without requiring a specific terminal host."""
    raw_pid = os.environ.get("NANAON_PARENT_PID")
    if not raw_pid:
        return
    try:
        parent_pid = int(raw_pid)
    except ValueError:
        return

    def watch_windows() -> None:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        synchronize = 0x00100000
        infinite = 0xFFFFFFFF
        handle = kernel32.OpenProcess(synchronize, False, parent_pid)
        if not handle:
            os._exit(0)
        try:
            kernel32.WaitForSingleObject(handle, infinite)
        finally:
            kernel32.CloseHandle(handle)
        os._exit(0)

    def watch_posix() -> None:
        import time

        while True:
            try:
                os.kill(parent_pid, 0)
            except OSError:
                os._exit(0)
            time.sleep(1)

    target = watch_windows if os.name == "nt" else watch_posix
    threading.Thread(target=target, name="launcher-guard", daemon=True).start()
