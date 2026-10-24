"""Cooperative workstation service control; never kills unrelated processes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from datetime import datetime

import httpx
from sqlalchemy import select, text

from .config import BASE_DIR, settings
from .db import SessionLocal
from .locking import FileLease, ResourceBusy
from .models import Job


class StopRequested(RuntimeError):
    pass


class JobCanceled(StopRequested):
    pass


def stop_path():
    return settings.data_dir / "services.stop"


def check_stop():
    if stop_path().exists():
        raise StopRequested("Services paused for shutdown. Resume this job after restart.")


def check_job_control(job_id):
    check_stop()
    if job_id:
        with SessionLocal() as db:
            status = db.scalar(select(Job.status).where(Job.id == job_id))
            if status in {"cancel_requested", "canceled"}:
                raise JobCanceled("Job canceled at a safe checkpoint.")
            if status == "stop_requested":
                raise StopRequested("Job stopped at a safe checkpoint.")


@contextmanager
def managed_service(kind: str):
    folder = settings.data_dir / "services"
    folder.mkdir(exist_ok=True)
    with FileLease(folder / f"{os.getpid()}.lock"):
        record = folder / f"{os.getpid()}.json"
        staged = record.with_suffix(".json.tmp")
        staged.write_text(json.dumps({"pid": os.getpid(), "kind": kind}), encoding="utf-8")
        staged.replace(record)
        try:
            yield
        finally:
            record.unlink(missing_ok=True)


def active_services():
    records = []
    for record in (settings.data_dir / "services").glob("*.json"):
        try:
            with FileLease(record.with_suffix(".lock")):
                continue  # Process exited; stale registry is not an active owner.
        except ResourceBusy:
            try:
                records.append(json.loads(record.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                # A record still being written or truncated by a crash is not an
                # authoritative owner; the lock above is the real gate.
                continue
    return records


def run_web(server):
    done = threading.Event()

    def watch():
        while not done.wait(0.25):
            if stop_path().exists():
                server.should_exit = True
                return

    check_stop()
    with managed_service("web"):
        thread = threading.Thread(target=watch, daemon=True)
        thread.start()
        try:
            server.run()
        finally:
            done.set()
            thread.join()


def start_web() -> dict:
    """Start the managed web app fully detached (no inherited console pipes).

    Service scripts call this instead of spawning PowerShell children, so a
    restarted server never keeps the operator's terminal or CI pipe open.
    """
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    stdout_path = log_dir / f"service-{stamp}.stdout"
    stderr_path = log_dir / f"service-{stamp}.stderr"
    stdout = stdout_path.open("ab")
    stderr = stderr_path.open("ab")
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "channel_press.cli", "serve"],
            cwd=str(BASE_DIR), stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
            creationflags=flags, close_fds=True,
        )
    finally:
        stdout.close()
        stderr.close()
    return {"pid": process.pid, "stdout": str(stdout_path), "stderr": str(stderr_path)}


def health():
    from .telegram.service import telegram_service

    result = {"services": active_services(), "paused": stop_path().exists(),
              "telegram": telegram_service.status(probe=False)}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            result["database"] = "Connected and tested"
            jobs = db.scalars(select(Job).where(Job.status.in_(["running", "failed_retryable", "paused"])))
            result["jobs"] = [{"id": job.id, "status": job.status, "stage": job.stage,
                               "error": job.error[:300]} for job in jobs]
    except Exception:
        result["database"] = "Unavailable or busy"
    try:
        response = httpx.get(settings.ollama_base_url.rstrip("/") + "/api/tags", timeout=2)
        response.raise_for_status()
        names = [model.get("name") for model in response.json().get("models", [])]
        result["ollama"] = "Connected and tested"
        result["model"] = "Available" if settings.ollama_model in names else "Configured model missing"
    except (httpx.HTTPError, ValueError):
        result["ollama"] = "Unavailable"
        result["model"] = "Not tested"
    return result


def validate() -> dict:
    """Configuration and storage readiness before starting any service."""
    checks = {}
    try:
        settings.ensure_dirs()
        probe = settings.data_dir / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks["data_dir"] = "Writable"
    except OSError:
        checks["data_dir"] = "Not writable"
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["database"] = "Accessible"
    except Exception:  # noqa: BLE001 - reported, never swallowed
        checks["database"] = "Unavailable"
    checks["credentials_configured"] = bool(
        str(settings.telegram_api_id).strip() and str(settings.telegram_api_hash).strip()
    )
    checks["session_present"] = settings.telegram_session_file.exists()
    checks["paused"] = stop_path().exists()
    return checks


def probe_source() -> dict:
    """Bounded read-only Telegram preflight on the configured source channel.

    Run as a short-lived process so the service restart sequence can initialise
    and verify the managed Telegram session without leaving a second owner.
    """
    from sqlalchemy import select as _select

    from .auth import active_rights
    from .models import Channel
    from .telegram.service import telegram_service
    from .telegram.transport import TelegramTransportError

    with SessionLocal() as db:
        channel = db.scalar(
            _select(Channel).where(Channel.is_demo.is_(False)).order_by(Channel.id).limit(1)
        )
        if channel is None:
            return {"status": "no_source", "detail": "No live source channel is registered."}
        if active_rights(db, channel.id) is None:
            return {
                "status": "authorization_missing",
                "channel": channel.handle,
                "detail": "Source authorization missing.",
            }
        handle = channel.handle
        expected = str(channel.telegram_channel_id or "")
    try:
        info = telegram_service.preflight(handle, expected_id=expected)
    except TelegramTransportError as exc:
        return {"status": "blocked", "channel": handle, "detail": str(exc)}
    finally:
        telegram_service.close()
    return {
        "status": "ready",
        "channel": info.get("channel"),
        "checked_at": info.get("checked_at"),
        "detail": "Telegram Ready",
    }


if __name__ == "__main__":
    import sys

    action = sys.argv[1] if len(sys.argv) > 1 else "health"
    if action == "stop":
        stop_path().touch()
        print("Stop requested. Managed services will finish their current safe checkpoint.")
    elif action == "resume":
        if active_services():
            raise SystemExit("Services still active; wait for graceful shutdown.")
        stop_path().unlink(missing_ok=True)
        print("Services resumed. No stop request remains.")
    elif action == "owners":
        services = active_services()
        print(json.dumps({"count": len(services), "services": services}))
    elif action == "validate":
        result = validate()
        print(json.dumps(result, indent=2))
        # A stop request is expected while restarting; only unusable storage or
        # an unreachable database is a hard failure.
        if result["database"] != "Accessible" or result["data_dir"] != "Writable":
            raise SystemExit(1)
    elif action == "probe":
        print(json.dumps(probe_source(), indent=2))
    elif action == "start-web":
        print(json.dumps(start_web()))
    else:
        print(json.dumps(health(), indent=2))
