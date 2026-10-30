from __future__ import annotations

import os
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

from .config import settings
from .logging_setup import get_logger

logger = get_logger("processes")
_lock = threading.RLock()
_owned: dict[int, subprocess.Popen] = {}


def hidden_process_options() -> dict:
    """Return Windows flags that prevent a child console from flashing."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {
        "creationflags": subprocess.CREATE_NO_WINDOW,
        "startupinfo": startupinfo,
    }


def worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).with_name("ChannelPressWorker.exe")
        if not executable.is_file():
            raise FileNotFoundError("The bundled Channel Press worker is missing.")
        return [str(executable)]
    return [sys.executable, "-m", "channel_press.cli", "worker", "--once"]


def launch_worker() -> tuple[subprocess.Popen, Path]:
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"worker-{datetime.now():%Y%m%d-%H%M%S}.log"
    handle = log_path.open("a", encoding="utf-8", buffering=1)
    environment = os.environ.copy()
    environment["CHANNEL_PRESS_WORKER_LOG_PATH"] = str(log_path)
    try:
        process = subprocess.Popen(
            worker_command(),
            env=environment,
            cwd=str(Path(sys.executable).resolve().parent)
            if getattr(sys, "frozen", False)
            else None,
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            close_fds=True,
            shell=False,
            **hidden_process_options(),
        )
    except OSError:
        handle.close()
        raise
    handle.close()
    with _lock:
        _owned[process.pid] = process
    threading.Thread(target=_watch, args=(process, log_path), daemon=True).start()
    return process, log_path


def _watch(process: subprocess.Popen, log_path: Path) -> None:
    try:
        code = process.wait()
        logger.info(
            "worker_process_exited",
            extra={"event": "worker_process_exited", "kind": "worker", "status": code},
        )
    except OSError:
        logger.exception("worker_process_wait_failed", extra={"kind": "worker"})
    finally:
        with _lock:
            _owned.pop(process.pid, None)


def owned_worker_processes() -> list[subprocess.Popen]:
    with _lock:
        for pid, process in tuple(_owned.items()):
            if process.poll() is not None:
                _owned.pop(pid, None)
        return list(_owned.values())


def pause_owned_workers(timeout: float = 90) -> bool:
    """Request checkpoint-safe worker stops and wait without killing children."""
    from .operations import stop_path

    workers = owned_worker_processes()
    if not workers:
        return True
    stop_path().touch()
    deadline = datetime.now().timestamp() + timeout
    for process in workers:
        remaining = max(0.0, deadline - datetime.now().timestamp())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            return False
    return not owned_worker_processes()
