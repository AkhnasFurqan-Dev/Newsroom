"""Crash-safe, nonblocking cross-process ownership (no credentials in lock files)."""
from __future__ import annotations

import hashlib
import logging
import os
import socket
from pathlib import Path


class ResourceBusy(RuntimeError):
    pass


def owner_process_alive(owner: str) -> bool | None:
    """Return None for another host, else whether the claimed local PID exists."""
    parts = (owner or "").split(":")
    if len(parts) < 2 or parts[0] != socket.gethostname():
        return None
    try:
        pid = int(parts[1])
    except ValueError:
        return None
    if pid <= 0:
        return None
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class FileLease:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise ResourceBusy("Resource currently owned by another Newsroom operation.") from exc
        logging.getLogger("channel_press.locks").info(
            "lease_acquired pid=%s resource=%s", os.getpid(),
            hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:12])
        return self

    def __exit__(self, *args):
        if self.handle is not None:
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
                logging.getLogger("channel_press.locks").info(
                    "lease_released pid=%s resource=%s", os.getpid(),
                    hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:12])
