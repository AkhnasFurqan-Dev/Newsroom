"""Recovery gates use disposable DBs and transports, never the owner's session."""
import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import select, text

from channel_press.collection.service import collect_window, get_or_create_window
from channel_press.db import SessionLocal, retry_busy
from channel_press.locking import FileLease
from channel_press.models import AuditEvent, Job, PublicationProfile
from channel_press.telegram.service import TelegramService
from channel_press.telegram.transport import TelegramTransportError
from tests.test_collection import PagedSource, _messages, _window_for
from tests.test_telegram_live import FakeTransport, _service


def test_source_status_never_connects_even_with_a_saved_session(tmp_path):
    fake = FakeTransport()
    service = _service(fake, tmp_path)
    service.session_path.touch()
    started = time.monotonic()
    for _ in range(10):
        assert service.status(probe=False)["authenticated"] is False
    assert time.monotonic() - started < 0.2
    assert not fake.connected
    assert service._loop is None


def test_cross_process_owner_blocks_before_opening_telegram(tmp_path):
    service = _service(FakeTransport(), tmp_path)
    lock = str(service.session_path.resolve()) + ".owner.lock"
    code = "from pathlib import Path; from channel_press.locking import FileLease; import sys; " \
           "lease=FileLease(Path(sys.argv[1])); lease.__enter__(); print('ready',flush=True); input()"
    child = subprocess.Popen([sys.executable, "-c", code, lock], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(TelegramTransportError, match="busy"):
            service.preflight("fixture_desk")
        child.communicate("stop\n", timeout=10)
        assert service.preflight("fixture_desk")["detail"] == "Telegram Ready"
    finally:
        if child.poll() is None:
            child.communicate("stop\n", timeout=10)
        service.close()
    assert service._loop is None


def test_timeout_disconnects_and_releases_owner(tmp_path, monkeypatch):
    from channel_press.config import settings
    monkeypatch.setattr(settings, "telegram_preflight_timeout_seconds", 0.05)

    class Offline(FakeTransport):
        async def resolve(self, handle):
            await asyncio.sleep(60)

    fake = Offline()
    service = _service(fake, tmp_path)
    try:
        with pytest.raises(TelegramTransportError, match="timed out"):
            service.preflight("fixture_desk")
        assert not fake.connected
        with FileLease(service.session_path.with_name(service.session_path.name + ".owner.lock")):
            pass
    finally:
        service.close()


@pytest.mark.parametrize("authorized,handle,reason", [
    (False, "fixture_desk", "not signed in"),
    (True, "private_channel", "private"),
])
def test_preflight_reports_auth_and_access_failure(tmp_path, authorized, handle, reason):
    service = _service(FakeTransport(authorized=authorized), tmp_path)
    try:
        with pytest.raises(TelegramTransportError, match=reason):
            service.preflight(handle)
    finally:
        service.close()


def test_collection_releases_dirty_writer_and_persists_checkpoints(db, demo_channel):
    window = _window_for(demo_channel)
    get_or_create_window(db, window)
    # Reproduce the old caller: dirty progress followed by a query autoflush.
    profile = db.get(PublicationProfile, 1)
    profile.city = "Changed before retrieval"
    db.flush()

    class ConcurrentSource(PagedSource):
        def fetch_window(self, *args, **kwargs):
            with SessionLocal() as other:
                profile = other.get(PublicationProfile, 1)
                profile.slogan = "Concurrent branding save"
                other.commit()
            return super().fetch_window(*args, **kwargs)

    stats = collect_window(db, demo_channel, window, ConcurrentSource(_messages(30)), rights=None)
    assert stats.created == 30
    db.refresh(profile)
    assert profile.slogan == "Concurrent branding save"


def test_busy_retry_uses_fresh_transaction_once(db, owner):
    holding = threading.Event()
    release = threading.Event()

    def writer():
        with SessionLocal() as side:
            side.execute(text("UPDATE publication_profiles SET city=city"))
            holding.set()
            release.wait(5)
            side.rollback()

    calls = []

    def save():
        calls.append(1)
        if len(calls) == 2:
            release.set()
        with SessionLocal() as side:
            side.add(AuditEvent(action="concurrency_test", object_type="system", object_id="0"))
            side.commit()

    with ThreadPoolExecutor() as pool:
        future = pool.submit(writer)
        assert holding.wait(3)
        try:
            retry_busy(save)
        finally:
            release.set()
        future.result()
    assert len(calls) == 2
    assert len(list(db.scalars(select(AuditEvent).where(AuditEvent.action == "concurrency_test")))) == 1


def test_running_source_get_and_mutations_are_responsive(client, auth, db, demo_channel):
    # The source stays stalled while HTTP operations execute on other connections.
    entered, release = threading.Event(), threading.Event()
    channel_id = demo_channel.id
    window = _window_for(demo_channel)

    class SlowSource(PagedSource):
        def fetch_window(self, *args, **kwargs):
            entered.set()
            assert release.wait(10)
            return super().fetch_window(*args, **kwargs)

    def collect():
        from channel_press.models import Channel
        with SessionLocal() as side:
            channel = side.get(Channel, channel_id)
            return collect_window(side, channel, window, SlowSource(_messages(30)), rights=None)

    with ThreadPoolExecutor() as pool:
        future = pool.submit(collect)
        assert entered.wait(3)
        try:
            start = time.monotonic()
            for url in [f"/sources/{channel_id}", "/jobs", "/schedules"]:
                assert client.get(url).status_code == 200
            response = client.post("/branding", data={"csrf_token": auth, "name": "Concurrent Desk"})
            assert response.status_code == 200
            assert time.monotonic() - start < 5
        finally:
            release.set()
        assert future.result().created == 30


def test_missing_session_preflight_cannot_create_a_job(client, auth, db, demo_channel, monkeypatch, tmp_path):
    import importlib
    web = importlib.import_module("channel_press.web.app")
    demo_channel.is_demo = False
    db.commit()
    service = TelegramService(api_id="123", api_hash="dummy", session_path=tmp_path / "missing.session")
    monkeypatch.setattr(web, "telegram_service", service)
    try:
        response = client.post("/build", data={"csrf_token": auth, "channel_id": demo_channel.id,
            "window_start": "2026-10-19T05:00", "window_end": "2026-10-20T05:00", "collect": "1"},
            follow_redirects=False)
        assert response.status_code == 303
        assert "session" in response.headers["location"]
        assert db.scalar(select(Job)) is None
    finally:
        service.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows native stderr gate")
def test_native_stderr_exit_code_is_preserved(tmp_path):
    script = tmp_path / "native.ps1"
    script.write_text('param([string]$Python,[string]$Out,[string]$Err,[int]$Code)\n'
        '$p=Start-Process -FilePath $Python -ArgumentList "-c `"import sys; print(\'INFO migration\',file=sys.stderr); sys.exit($Code)`"" '
        '-Wait -PassThru -NoNewWindow -RedirectStandardOutput $Out -RedirectStandardError $Err\nexit $p.ExitCode\n')
    for code in (0, 7):
        result = subprocess.run(["powershell", "-NoProfile", "-File", str(script), "-Python", sys.executable,
            "-Out", str(tmp_path / "out"), "-Err", str(tmp_path / "err"), "-Code", str(code)], capture_output=True)
        assert result.returncode == code
        assert "INFO migration" in (tmp_path / "err").read_text()


def test_crashed_process_releases_kernel_lease(tmp_path):
    from channel_press.locking import ResourceBusy
    path = tmp_path / "disposable.lock"
    code = "from pathlib import Path; from channel_press.locking import FileLease; import sys; " \
           "lease=FileLease(Path(sys.argv[1])); lease.__enter__(); print('ready',flush=True); input()"
    child = subprocess.Popen([sys.executable, "-c", code, str(path)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        with pytest.raises(ResourceBusy):
            with FileLease(path):
                pass
        # Deliberately crash ONLY this disposable test child, not app processes.
        child.kill()
        child.wait(timeout=10)
        # Windows completes kernel handle teardown asynchronously after exit.
        deadline = time.monotonic() + 5
        while True:
            try:
                with FileLease(path):
                    break
            except ResourceBusy:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
    finally:
        if child.poll() is None:
            child.communicate("stop\n", timeout=10)


def test_preflight_success_then_network_failure_releases_owner(tmp_path):
    fake = FakeTransport()
    service = _service(fake, tmp_path)
    try:
        assert service.preflight("fixture_desk")["detail"] == "Telegram Ready"
        async def offline(*args, **kwargs):
            raise TelegramTransportError("Network disconnected")
        fake.page = offline
        from tests.test_telegram_live import START, END
        with pytest.raises(TelegramTransportError, match="Network disconnected"):
            service.fetch_window("fixture_desk", START, END)
        assert not fake.connected
    finally:
        service.close()


def test_duplicate_click_does_not_rearm_running_worker(db, demo_channel):
    from channel_press.scheduling.service import enqueue_manual_job
    from tests.test_telegram_live import START, END
    first = enqueue_manual_job(db, demo_channel, window_start=START, window_end=END)
    first.status = "running"
    first.lock_owner = "active-worker"
    first.attempt = 1
    db.commit()
    second = enqueue_manual_job(db, demo_channel, window_start=START, window_end=END)
    db.commit()
    assert second.id == first.id
    assert second.status == "running"
    assert second.lock_owner == "active-worker"
    assert second.attempt == 1


def test_duplicate_sync_window_uses_one_job(db, demo_channel):
    from channel_press.scheduling.service import enqueue_collect_job
    window = _window_for(demo_channel)
    first = enqueue_collect_job(db, demo_channel, window=window, mode="sync_window")
    db.commit()
    second = enqueue_collect_job(db, demo_channel, window=window, mode="sync_window")
    db.commit()
    assert first.id == second.id


def test_stop_during_collection_retains_checkpoint_and_can_resume(db, demo_channel):
    from channel_press.operations import StopRequested, stop_path
    from channel_press.models import RetrievalCheckpoint, SourcePost
    window = _window_for(demo_channel)

    class StoppingSource(PagedSource):
        def fetch_window(self, window, *, on_page=None, resume_offset=0):
            def page(value):
                on_page(value)
                stop_path().touch()
            return super().fetch_window(window, on_page=page, resume_offset=resume_offset)

    try:
        with pytest.raises(StopRequested):
            collect_window(db, demo_channel, window, StoppingSource(_messages(30), page_size=10), rights=None)
        cp = db.scalar(select(RetrievalCheckpoint))
        assert cp.cursor_message_id
        assert cp.status == "partial"
    finally:
        stop_path().unlink(missing_ok=True)
    result = collect_window(db, demo_channel, window, PagedSource(_messages(30), page_size=10), rights=None)
    assert result.fetched == 30
    assert len(list(db.scalars(select(SourcePost)))) == 30


def test_cancel_running_job_requests_checkpoint_and_finished_job_is_immutable(client, auth, db, demo_channel):
    from channel_press.scheduling.service import enqueue_manual_job
    from channel_press.operations import check_job_control, JobCanceled
    from tests.test_telegram_live import START, END
    job = enqueue_manual_job(db, demo_channel, window_start=START, window_end=END)
    job.status = "running"
    db.commit()
    response = client.post(f"/jobs/{job.id}/cancel", data={"csrf_token": auth}, follow_redirects=False)
    assert response.status_code == 303
    with pytest.raises(JobCanceled):
        check_job_control(job.id)
    db.refresh(job)
    job.status = "completed"
    db.commit()
    response = client.post(f"/jobs/{job.id}/cancel", data={"csrf_token": auth}, follow_redirects=False)
    assert "cannot" in response.headers["location"]
    db.refresh(job)
    assert job.status == "completed"


def test_exhausted_busy_is_friendly_503(client, auth):
    entered, release = threading.Event(), threading.Event()
    def writer():
        with SessionLocal() as side:
            side.execute(text("UPDATE publication_profiles SET city=city"))
            entered.set()
            release.wait(5)
            side.rollback()
    with ThreadPoolExecutor() as pool:
        future = pool.submit(writer)
        assert entered.wait(3)
        try:
            response = client.post("/branding", data={"csrf_token": auth, "name": "Busy save"})
            assert response.status_code == 503
            assert "not saved" in response.text
            assert "Retry-After" in response.headers
        finally:
            release.set()
        future.result()


def _dead_pid() -> int:
    """PID of a process that has exited, so ownership checks can prove absence."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def test_crashed_worker_lease_is_recovered_but_live_owner_is_not_stolen(db, demo_channel):
    from datetime import timedelta

    from channel_press.scheduling.service import enqueue_manual_job, release_expired_locks
    from channel_press.timeutil import utcnow
    from tests.test_telegram_live import END, START

    dead = enqueue_manual_job(db, demo_channel, window_start=START, window_end=END)
    dead.status = "running"
    dead.lock_owner = f"{socket.gethostname()}:{_dead_pid()}:crashed"
    dead.lock_expires_at = utcnow() - timedelta(minutes=5)
    dead.heartbeat_at = utcnow() - timedelta(minutes=30)
    live = enqueue_manual_job(db, demo_channel, window_start=END, window_end=END + timedelta(days=1))
    live.status = "running"
    live.lock_owner = f"{socket.gethostname()}:{os.getpid()}:alive"
    live.lock_expires_at = utcnow() - timedelta(minutes=5)
    live.heartbeat_at = utcnow() - timedelta(minutes=30)
    db.commit()

    assert release_expired_locks(db) == 1
    db.refresh(dead)
    db.refresh(live)
    assert dead.status == "failed_retryable"
    assert dead.lock_owner == "" and dead.lock_expires_at is None
    assert live.status == "running"
    assert live.lock_owner.endswith(":alive")


def test_cancel_requested_job_does_not_become_a_zombie(db, demo_channel):
    from datetime import timedelta

    from channel_press.scheduling.service import enqueue_manual_job, release_expired_locks
    from channel_press.timeutil import utcnow
    from tests.test_telegram_live import END, START

    job = enqueue_manual_job(db, demo_channel, window_start=START, window_end=END)
    job.status = "cancel_requested"
    job.lock_owner = f"{socket.gethostname()}:{_dead_pid()}:crashed"
    job.lock_expires_at = utcnow() - timedelta(minutes=5)
    job.heartbeat_at = utcnow() - timedelta(minutes=30)
    db.commit()

    assert release_expired_locks(db) == 1
    db.refresh(job)
    assert job.status == "canceled"
    assert job.lock_owner == ""
    # The window and its checkpoint survive so an operator can retry it.
    assert job.error


def test_window_claim_is_exclusive_and_recoverable(db, demo_channel):
    from channel_press.collection.service import claim_window, get_or_create_window, release_window

    window = _window_for(demo_channel)
    record = get_or_create_window(db, window)
    db.commit()
    assert claim_window(db, record, owner="worker-a")
    db.commit()
    assert not claim_window(db, record, owner="worker-b")
    release_window(db, record, status="failed")
    db.commit()
    assert claim_window(db, record, owner="worker-b")


def test_failed_sync_window_is_rearmed_instead_of_returned_dead(db, demo_channel):
    from channel_press.scheduling.service import enqueue_collect_job

    window = _window_for(demo_channel)
    first = enqueue_collect_job(db, demo_channel, window=window, mode="sync_window")
    db.commit()
    first.status = "failed_terminal"
    first.attempt = 2
    first.error = "Network disconnected"
    db.commit()

    second = enqueue_collect_job(db, demo_channel, window=window, mode="sync_window")
    db.commit()
    assert second.id == first.id
    assert second.status == "scheduled"
    assert second.attempt == 0
    assert second.error == ""


def test_job_polling_and_source_page_stay_responsive_while_collecting(
    client, auth, db, demo_channel
):
    from channel_press.models import Channel
    from channel_press.scheduling.service import enqueue_manual_job
    from tests.test_telegram_live import END, START

    job = enqueue_manual_job(db, demo_channel, window_start=START, window_end=END)
    db.commit()
    entered, release = threading.Event(), threading.Event()
    window = _window_for(demo_channel)
    channel_id = demo_channel.id

    class SlowSource(PagedSource):
        def fetch_window(self, *args, **kwargs):
            entered.set()
            assert release.wait(10)
            return super().fetch_window(*args, **kwargs)

    def collect():
        with SessionLocal() as side:
            channel = side.get(Channel, channel_id)
            return collect_window(side, channel, window, SlowSource(_messages(30)), rights=None)

    with ThreadPoolExecutor() as pool:
        future = pool.submit(collect)
        assert entered.wait(3)
        try:
            start = time.monotonic()
            responses = [client.get(f"/sources/{channel_id}"), client.get("/jobs"),
                         client.get(f"/api/jobs/{job.id}")]
            assert [response.status_code for response in responses] == [200, 200, 200]
            assert responses[2].json()["status"] != "running"
            assert time.monotonic() - start < 5
        finally:
            release.set()
        assert future.result().created == 30


def test_live_build_preflight_busy_session_creates_no_job(client, auth, db, demo_channel):
    import importlib

    web = importlib.import_module("channel_press.web.app")
    demo_channel.is_demo = False
    db.commit()
    lock = str(web.telegram_service.session_path.resolve()) + ".owner.lock"
    code = (
        "from pathlib import Path; from channel_press.locking import FileLease; import sys; "
        "lease=FileLease(Path(sys.argv[1])); lease.__enter__(); print('ready',flush=True); input()"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", code, lock],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        response = client.post(
            "/build",
            data={"csrf_token": auth, "channel_id": demo_channel.id,
                  "window_start": "2026-10-19T05:00", "window_end": "2026-10-20T05:00",
                  "collect": "1"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers["location"].lower()
        assert "busy" in location or "session" in location
        assert db.scalar(select(Job)) is None
    finally:
        child.communicate("stop\n", timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="Windows worker launcher")
def test_real_worker_launcher_keeps_info_stderr_and_true_exit_code(tmp_path):
    script = (Path(__file__).resolve().parents[1] / "scripts" / "run-worker.ps1")
    root = tmp_path / "root"
    (root / "data" / "logs").mkdir(parents=True)
    (root / "worker_probe.py").write_text(
        "import sys\n"
        "print('INFO  [alembic.runtime.migration] Context impl SQLiteImpl.', file=sys.stderr)\n"
        "sys.exit(int(sys.argv[1]))\n",
        encoding="utf-8",
    )
    for expected in (0, 7):
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-Root", str(root), "-Python", sys.executable,
             "-WorkerArgs", f"worker_probe.py {expected}"],
            capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == expected, result.stderr
        stderr_logs = list((root / "data" / "logs").glob("worker-err-*.log"))
        assert stderr_logs
        assert "INFO" in stderr_logs[-1].read_text(encoding="utf-8-sig")


def test_sync_window_is_not_queued_when_preflight_fails(client, auth, db, monkeypatch):
    import importlib

    from channel_press.models import Job, User
    from tests.test_build_queue import _live_channel, _schedule

    web = importlib.import_module("channel_press.web.app")
    live = _live_channel(db, db.scalar(select(User).order_by(User.id)))
    _schedule(db, live)
    monkeypatch.setattr(web, "_telegram_ready", lambda db, channel: "Telegram connection timed out.")

    response = client.post(f"/sources/{live.id}/telegram/sync", data={"csrf_token": auth},
                           follow_redirects=False)

    assert response.status_code == 303
    assert "not+queued" in response.headers["location"] or "not%20queued" in response.headers["location"]
    assert db.scalar(select(Job)) is None


def test_dashboard_owner_sees_health_controls(client, auth):
    response = client.get("/")
    assert response.status_code == 200
    for label in ("System Health", "Check Connections", "Restart Channel Press Services"):
        assert label in response.text


def test_system_check_separates_configured_from_tested(client, auth, monkeypatch):
    from channel_press.config import settings

    monkeypatch.setattr(settings, "ollama_base_url", "http://127.0.0.1:9")
    response = client.post("/system/check", data={"csrf_token": auth})
    assert response.status_code == 200
    report = response.json()
    assert report["web"] == "Connected and tested"
    assert report["database"] == "Connected and tested"
    assert report["source_channel"] == "Source authorization missing"
    assert report["ollama"] == "Unavailable"
    # Credentials may be configured without a tested session; never claim success.
    assert report["telegram"]["authenticated"] is False


def test_system_restart_requires_confirmation_and_starts_the_documented_script(
    client, auth, monkeypatch
):
    import subprocess as sp

    assert client.post("/system/restart", data={"csrf_token": auth}).status_code == 400
    calls = []

    class FakeProcess:
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))

    monkeypatch.setattr(sp, "Popen", FakeProcess)
    response = client.post("/system/restart",
                           data={"csrf_token": auth, "confirm": "restart"})
    assert response.status_code == 200
    assert calls, "restart was never launched"
    command, options = calls[0]
    assert any("restart-services.ps1" in str(part) for part in command)
    assert options["creationflags"]


def test_edition_defers_instead_of_failing_when_another_collector_owns_the_window(
    db, owner, monkeypatch
):
    from datetime import timedelta

    from channel_press.collection.service import claim_window, get_or_create_window
    from channel_press.models import SourcePost
    from channel_press.scheduling import service as scheduling
    from channel_press.scheduling.service import enqueue_collect_job, worker_once
    from channel_press.timeutil import utcnow
    from tests.test_build_queue import _live_channel

    live = _live_channel(db, owner, slug="defer-desk")
    end = utcnow() - timedelta(minutes=1)
    window = _window_for(live)
    job = enqueue_collect_job(db, live, window=window, mode="sync_window")
    db.commit()
    record = get_or_create_window(db, window, job_id=job.id)
    assert claim_window(db, record, owner="other-worker:999:busy")
    db.commit()

    class MustNotFetch:
        name = "telegram"

        def __init__(self, *args, **kwargs):
            raise AssertionError("a deferred edition must not start a second collection")

    monkeypatch.setattr(scheduling, "LiveTelegramSource", MustNotFetch)
    stats = worker_once(db, now=end + timedelta(seconds=30), max_jobs=1)
    db.commit()
    db.refresh(job)

    assert stats.failed == 0
    assert job.status == "scheduled"
    assert job.stage == "waiting_for_collector"
    assert "already active" in job.error.lower()
    assert job.lock_owner == ""
    assert job.attempt == 0
    assert db.scalar(select(SourcePost)) is None
