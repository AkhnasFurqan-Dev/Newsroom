"""Regression tests for the Build page and the Queue Build request.

The original defect: ``build_submit`` read ``PublicationProfile.cutoff_local``,
an attribute that does not exist (the cutoff lives on ``EditionSchedule``). The
request raised ``AttributeError`` after the job was flushed but before it was
committed, so the operator saw a bare 500 and no job existed. These tests pin
the fixed behaviour end to end:

* the live channel is selected by default and the window is the last
  cutoff-to-cutoff interval;
* one POST creates exactly one durable job for the requested channel and the
  exact half-open interval;
* a duplicate POST reuses that job and cannot create a second edition;
* invalid windows, missing rights, unknown channels and missing CSRF fail with
  a clear response rather than a 500;
* manual builds are always staged for review and never auto-published;
* unexpected exceptions are logged with a traceback and a safe reference id;
* worker failures and interruptions leave diagnostics on the job page.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote_plus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from channel_press.collection.service import preview_window
from channel_press.config import settings
from channel_press.logging_setup import configure_logging
from channel_press.models import (
    Channel,
    Edition,
    EditionSchedule,
    EditionWindowRecord,
    Job,
    RightsGrant,
)
from channel_press.scheduling.service import enqueue_manual_job, worker_once
from channel_press.timeutil import to_local
from channel_press.web.app import app as fastapi_app


@pytest.fixture
def stub_launch(monkeypatch):
    """Keep route tests from running the real worker inline.

    ``CHANNEL_PRESS_SYNC_JOBS=1`` is set by the test harness, so without this
    the POST would build an edition before the assertions run. The end-to-end
    build is covered by ``test_manual_build_waits_for_review_and_never_publishes``.
    """
    calls: list[bool] = []
    app_module = sys.modules["channel_press.web.app"]

    def fake_launch(background: bool = True) -> str:
        calls.append(background)
        return "Stub worker started."

    monkeypatch.setattr(app_module, "_launch_worker", fake_launch)
    return calls


def _live_channel(db, owner, *, with_rights: bool = True, slug: str = "live-desk") -> Channel:
    channel = Channel(slug=slug, title="Live Desk", handle="@live_desk", is_demo=False)
    db.add(channel)
    db.flush()
    if with_rights:
        db.add(
            RightsGrant(
                channel_id=channel.id,
                grantor_name="Owner of the channel",
                authority_basis="The owner supplied the channel credentials and authorized processing.",
                scope_json=json.dumps(["ingest", "ai_process", "publish_local"]),
                terms_version="CP-CONTENT-2026-01",
                evidence_ref="tests/test_build_queue.py",
                created_by=owner.id,
            )
        )
        db.flush()
    db.commit()
    return channel


def _schedule(db, channel: Channel, **overrides) -> EditionSchedule:
    values = {
        "kind": "daily",
        "timezone": "Asia/Karachi",
        "cutoff_time": "05:00",
        "release_time": "08:00",
        "weekdays": "0,1,2,3,4,5,6",
        "enabled": False,
        "requires_review": True,
        "auto_publish_nonrisk": False,
    }
    values.update(overrides)
    schedule = EditionSchedule(channel_id=channel.id, **values)
    db.add(schedule)
    db.commit()
    return schedule


def _form(page: str) -> dict:
    def value(name: str) -> str:
        match = re.search(rf'id="{name}"[^>]*value="([^"]*)"', page)
        assert match, f"{name} not found in build page"
        return match.group(1)

    return {"window_start": value("window_start"), "window_end": value("window_end")}


def _post_build(client, csrf: str, channel: Channel, **overrides):
    data = {
        "csrf_token": csrf,
        "channel_id": str(channel.id),
        "window_start": "2026-10-19T05:00",
        "window_end": "2026-10-20T05:00",
        "kind": "manual",
        "title": "",
        "publish": "publish",
        "collect": "0",
    }
    data.update(overrides)
    return client.post("/build", data=data, follow_redirects=False)


# ---------------------------------------------------------------------------
# successful queueing
# ---------------------------------------------------------------------------


def test_build_page_defaults_to_the_live_channel_and_cutoff_window(client, db, owner, auth):
    live = _live_channel(db, owner)
    schedule = _schedule(db, live)
    preview = preview_window(db, schedule)
    expected_start = to_local(datetime.fromisoformat(preview["window"]["start_utc"]), "Asia/Karachi")
    expected_end = to_local(datetime.fromisoformat(preview["window"]["end_utc"]), "Asia/Karachi")

    response = client.get("/build")

    assert response.status_code == 200
    assert f'<option value="{live.id}" selected>' in response.text
    assert "No daily schedule for this channel" not in response.text
    form = _form(response.text)
    assert form["window_start"] == expected_start.strftime("%Y-%m-%dT%H:%M")
    assert form["window_end"] == expected_end.strftime("%Y-%m-%dT%H:%M")


def test_build_page_without_a_schedule_uses_the_last_cutoff_interval(client, db, owner, auth):
    live = _live_channel(db, owner)

    response = client.get(f"/build?channel={live.id}")

    assert response.status_code == 200
    assert "No daily schedule for this channel" in response.text
    form = _form(response.text)
    start = datetime.strptime(form["window_start"], "%Y-%m-%dT%H:%M")
    end = datetime.strptime(form["window_end"], "%Y-%m-%dT%H:%M")
    assert end - start == timedelta(days=1)
    assert (start.hour, start.minute) == (5, 0)
    assert (end.hour, end.minute) == (5, 0)


def test_queue_build_creates_one_job_with_the_exact_window(client, db, owner, auth, stub_launch):
    live = _live_channel(db, owner)
    _schedule(db, live)
    page = client.get(f"/build?channel={live.id}")
    form = _form(page.text)
    form["channel_id"] = str(live.id)
    form["csrf_token"] = auth
    form["collect"] = "0"
    form["kind"] = "manual"

    response = client.post("/build", data=form, follow_redirects=False)

    assert response.status_code == 303, response.text
    location = response.headers["location"]
    match = re.match(r"^/jobs/(\d+)\?msg=", location)
    assert match, location
    job_id = int(match.group(1))

    job = db.get(Job, job_id)
    assert job is not None
    assert job.channel_id == live.id
    assert job.kind == "edition"
    assert job.status == "scheduled"
    payload = json.loads(job.payload_json)
    expected_start = datetime.strptime(form["window_start"], "%Y-%m-%dT%H:%M").replace(
        tzinfo=UTC
    ) - timedelta(hours=5)
    expected_end = datetime.strptime(form["window_end"], "%Y-%m-%dT%H:%M").replace(
        tzinfo=UTC
    ) - timedelta(hours=5)
    assert datetime.fromisoformat(payload["window_start"]) == expected_start
    assert datetime.fromisoformat(payload["window_end"]) == expected_end
    assert payload["requires_review"] is True
    assert payload["auto_publish_nonrisk"] is False
    assert payload["cutoff_local"] == "05:00"

    record = db.scalar(
        select(EditionWindowRecord).where(EditionWindowRecord.window_key == payload["window_key"])
    )
    assert record is not None
    assert record.channel_id == live.id
    assert record.job_id == job.id


def test_duplicate_queue_build_reuses_the_same_job(client, db, owner, auth, stub_launch):
    live = _live_channel(db, owner)
    data = {
        "csrf_token": auth,
        "channel_id": str(live.id),
        "window_start": "2026-10-19T05:00",
        "window_end": "2026-10-20T05:00",
        "kind": "manual",
        "collect": "0",
    }

    first = client.post("/build", data=data, follow_redirects=False)
    second = client.post("/build", data=data, follow_redirects=False)

    assert first.status_code == 303 and second.status_code == 303
    assert first.headers["location"].split("?")[0] == second.headers["location"].split("?")[0]
    assert db.scalar(select(Job).where(Job.channel_id == live.id)) is not None
    assert db.query(Job).count() == 1
    assert db.query(Edition).count() == 0
    assert "no duplicate" in unquote_plus(second.headers["location"]).lower()


# ---------------------------------------------------------------------------
# rejected requests
# ---------------------------------------------------------------------------


def test_invalid_cutoff_is_a_form_error_not_a_crash(client, db, owner, auth):
    live = _live_channel(db, owner)
    for window_end in ("2026-10-19T05:00", "not-a-date"):
        response = _post_build(client, auth, live, window_end=window_end)
        assert response.status_code == 303, response.text
        assert "err=" in response.headers["location"]
        assert response.headers["location"].startswith(f"/build?channel={live.id}")
    assert db.query(Job).count() == 0


def test_missing_rights_blocks_the_job(client, db, owner, auth):
    live = _live_channel(db, owner, with_rights=False)
    response = _post_build(client, auth, live)
    assert response.status_code == 303
    assert "rights" in response.headers["location"].lower()
    assert db.query(Job).count() == 0


def test_unknown_channel_is_404(client, db, owner, auth):
    response = _post_build(client, auth, Channel(id=999, slug="missing", title="Missing"))
    assert response.status_code == 404


def test_queue_build_requires_csrf(client, db, owner, auth):
    live = _live_channel(db, owner)
    response = client.post(
        "/build",
        data={
            "channel_id": str(live.id),
            "window_start": "2026-10-19T05:00",
            "window_end": "2026-10-20T05:00",
        },
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert db.query(Job).count() == 0


def test_overlong_window_is_rejected(client, db, owner, auth):
    live = _live_channel(db, owner)
    response = _post_build(
        client, auth, live, window_start="2026-01-01T05:00", window_end="2026-10-20T05:00"
    )
    assert response.status_code == 303
    assert "31 days" in unquote_plus(response.headers["location"])
    assert db.query(Job).count() == 0


# ---------------------------------------------------------------------------
# publication safety
# ---------------------------------------------------------------------------


def test_manual_build_waits_for_review_and_never_publishes(client, db, demo_channel, owner, auth, sample_import):
    response = _post_build(
        client,
        auth,
        demo_channel,
        window_start="2026-10-19T05:00",
        window_end="2026-10-21T05:00",
    )
    assert response.status_code == 303, response.text
    job = db.scalar(select(Job).order_by(Job.id.desc()))
    assert job is not None
    assert job.status == "awaiting_review"
    edition = db.get(Edition, job.edition_id)
    assert edition is not None
    assert edition.status == "awaiting_review"
    assert edition.published_at is None


def test_a_window_with_a_built_edition_is_not_queued_again(client, db, demo_channel, owner, auth, sample_import):
    first = _post_build(
        client,
        auth,
        demo_channel,
        window_start="2026-10-19T05:00",
        window_end="2026-10-21T05:00",
    )
    assert first.status_code == 303, first.text
    job = db.scalar(select(Job).order_by(Job.id.desc()))
    edition = db.get(Edition, job.edition_id)
    record = db.scalar(select(EditionWindowRecord).where(EditionWindowRecord.job_id == job.id))
    assert record is not None
    assert record.edition_id == edition.id
    # Normal end state of a built window: the record owns an edition and no
    # runnable job remains. Moving the key hides it from the deterministic
    # lookup without deleting the audit history.
    job.key = "manual:historic:already:built"
    record.job_id = None
    db.commit()

    second = _post_build(
        client,
        auth,
        demo_channel,
        window_start="2026-10-19T05:00",
        window_end="2026-10-21T05:00",
    )
    assert second.status_code == 303
    location = unquote_plus(second.headers["location"])
    assert "already built" in location
    assert f"edition #{edition.id}" in location
    assert db.query(Job).count() == 1
    assert db.query(Edition).count() == 1


# ---------------------------------------------------------------------------
# worker diagnostics and recovery
# ---------------------------------------------------------------------------


def test_worker_failure_is_recorded_with_a_file_and_line(db, owner):
    configure_logging("worker", console=False)
    live = _live_channel(db, owner)
    job = enqueue_manual_job(
        db,
        live,
        window_start=datetime(2026, 10, 19, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 10, 20, 0, 0, tzinfo=UTC),
        collect=False,
    )
    db.commit()

    stats = worker_once(db, max_jobs=1)
    db.commit()

    assert stats.processed == 0 and stats.failed == 1
    db.refresh(job)
    assert job.status == "failed_terminal"
    assert job.error.startswith("PipelineError:")
    assert re.search(r"\[at \w+\.py:\d+ in \w+\]$", job.error), job.error

    log_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((settings.data_dir / "logs").glob("worker-*.log"))
    )
    assert f'"job_id": {job.id}' in log_text
    assert '"event": "job_failed"' in log_text
    assert "PipelineError" in log_text


def test_worker_resumes_after_an_interrupted_lease(db, demo_channel, sample_import):
    now = datetime.now(UTC)
    job = enqueue_manual_job(
        db,
        demo_channel,
        window_start=datetime(2026, 10, 19, 0, 0, tzinfo=UTC),
        window_end=datetime(2026, 10, 21, 0, 0, tzinfo=UTC),
    )
    db.commit()
    job.status = "running"
    job.attempt = 1
    job.max_attempts = 3
    job.stage = "drafting"
    job.lock_owner = "crashed-worker"
    job.lock_expires_at = now - timedelta(minutes=5)
    db.commit()

    stats = worker_once(db, max_jobs=1)
    db.commit()

    db.refresh(job)
    assert stats.stale_locks == 1
    assert job.status == "awaiting_review"
    assert job.edition_id is not None
    assert job.lock_owner == ""
    assert job.attempt == 2


def test_failed_manual_job_is_requeued_in_place(db, owner):
    live = _live_channel(db, owner)
    window = {
        "window_start": datetime(2026, 10, 19, 0, 0, tzinfo=UTC),
        "window_end": datetime(2026, 10, 20, 0, 0, tzinfo=UTC),
    }
    first = enqueue_manual_job(db, live, collect=False, **window)
    db.commit()
    first.status = "failed_terminal"
    first.error = "synthetic failure"
    db.commit()

    second = enqueue_manual_job(db, live, collect=False, **window)
    db.commit()

    assert second.id == first.id
    assert db.query(Job).count() == 1
    assert second.status == "scheduled"
    assert second.attempt == 0
    assert second.error == ""
    assert second.requeued_existing is True


def test_worker_launch_failure_keeps_the_job_queued(client, db, owner, auth, monkeypatch):
    live = _live_channel(db, owner)
    app_module = sys.modules["channel_press.web.app"]

    def explode(*args, **kwargs):
        raise OSError("cannot spawn process")

    monkeypatch.setattr(app_module.subprocess, "Popen", explode)
    monkeypatch.delenv("CHANNEL_PRESS_SYNC_JOBS", raising=False)

    response = _post_build(client, auth, live)
    assert response.status_code == 303, response.text
    assert "could not start" in unquote_plus(response.headers["location"]).lower()
    job = db.scalar(select(Job).order_by(Job.id.desc()))
    assert job is not None and job.status == "scheduled"
    log_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((settings.data_dir / "logs").glob("web-*.log"))
    )
    assert '"event": "worker_launch_failed"' in log_text


# ---------------------------------------------------------------------------
# unexpected exceptions are logged with a traceback, never exposed
# ---------------------------------------------------------------------------


def test_unhandled_exception_returns_a_safe_page_and_logs_the_traceback(client, db, owner, auth, monkeypatch):
    live = _live_channel(db, owner)
    app_module = sys.modules["channel_press.web.app"]

    def explode(*args, **kwargs):
        raise RuntimeError("secret-marker-should-not-reach-the-browser")

    monkeypatch.setattr(app_module, "enqueue_manual_job", explode)
    quiet_client = TestClient(fastapi_app, base_url="http://testserver", raise_server_exceptions=False)
    quiet_client.cookies.update(client.cookies)
    try:
        response = quiet_client.post(
            "/build",
            data={
                "csrf_token": auth,
                "channel_id": str(live.id),
                "window_start": "2026-10-19T05:00",
                "window_end": "2026-10-20T05:00",
                "kind": "manual",
                "collect": "0",
            },
            follow_redirects=False,
        )
    finally:
        quiet_client.close()

    assert response.status_code == 500
    assert "Reference" in response.text
    assert "secret-marker" not in response.text
    assert "Traceback" not in response.text

    log_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((settings.data_dir / "logs").glob("web-*.log"))
    )
    assert '"event": "unhandled_exception"' in log_text
    assert "RuntimeError: secret-marker-should-not-reach-the-browser" in log_text
    assert "test_build_queue.py" in log_text
