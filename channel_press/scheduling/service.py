"""Edition scheduling, idempotent job creation and the short-lived worker.

The design is intentionally conservative for a single Windows workstation:

* Jobs have a deterministic key (channel + edition kind + cutoff), so repeated
  scheduler runs cannot create duplicate editions.
* A job is claimed with a renewable lease and a process-level file lock ensures
  two workers never run at once.
* A job builds an edition at the cutoff. If review is required, the output is
  staged and the edition waits for a human decision. Auto-publication is only
  allowed for low-risk editions with no open blocking flags.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import traceback
import uuid
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from ..auth import active_rights, audit
from ..db import SessionLocal
from ..collection import window as window_math
from ..collection.service import (
    CollectionStats,
    EditionWindow,
    LiveTelegramSource,
    claim_window,
    collect_window,
    commit_edition_window,
    get_or_create_window,
    mark_no_content,
    mark_window_built,
    record_for_edition,
)
from ..config import settings
from ..locking import owner_process_alive
from ..logging_setup import get_logger
from ..models import (
    Article,
    Channel,
    Edition,
    EditionArticle,
    EditionSchedule,
    EditionWatermark,
    EditionWindowRecord,
    Job,
    JobStep,
    PublicationAttempt,
    PublicationProfile,
    RetryAttempt,
)
from ..pipeline import PipelineError, PipelineResult, run_pipeline
from ..publications import (
    default_publication,
    publication_channels,
    publication_for_edition,
)
from ..timeutil import to_local, utcnow
from ..publishing.service import PublicationError, edition_review_blockers

JOB_RUNNABLE = {"scheduled", "failed_retryable", "resumable"}
JOB_ALREADY_DONE = {"awaiting_review", "published", "completed", "no_content"}

logger = get_logger("worker")

_PROGRESS_RE = re.compile(r"\b(\d+)\s*/\s*(\d+)\b")
_STAGE_BASE = {
    "queued": 0, "source_validation": 3, "collection": 12,
    "normalization": 20, "classification": 22, "clustering": 28, "drafting": 35,
    "auditing": 72, "media_prepare": 75, "media_download": 77, "media_process": 82,
    "layout": 84, "quality_check": 95, "clean_media": 99,
    "layout_prepare": 84, "layout_compile_html": 85, "layout_launch_browser": 86,
    "layout_load_page": 87, "layout_wait_for_pagination": 89,
    "layout_generate_pdf": 92, "layout_validate_pdf": 94,
    "layout_generate_epaper": 96, "layout_complete": 97,
    "publishing": 98, "review": 100, "published": 100, "completed": 100,
}


def _progress_value(stage: str, detail: str) -> int:
    base = _STAGE_BASE.get(stage, 0)
    match = _PROGRESS_RE.search(detail or "")
    if match:
        current, total = (int(value) for value in match.groups())
        if total > 0 and stage == "drafting":
            return min(70, 35 + round(current / total * 35))
    return base


def progress_snapshot(job: Job) -> dict:
    payload = json.loads(job.result_json or "{}")
    saved = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
    now = utcnow()
    elapsed = saved.get("elapsed_seconds", 0)
    if job.started_at:
        elapsed = max(float(elapsed or 0), (now - job.started_at).total_seconds())
    percent = int(saved.get("percent") or _progress_value(job.stage, saved.get("detail", "")))
    stale = bool(
        job.status == "running"
        and job.heartbeat_at
        and (now - job.heartbeat_at).total_seconds() > settings.worker_heartbeat_timeout_seconds
        and owner_process_alive(job.lock_owner) is not True
    )
    detail = saved.get("detail") or (
        "Worker heartbeat is stale; restart or retry the job."
        if stale else f"Worker is active in {job.stage.replace('_', ' ')}…"
    )
    return {
        "percent": min(100, max(0, percent)),
        "detail": detail,
        "elapsed_seconds": round(elapsed, 1),
        "eta_seconds": saved.get("eta_seconds"),
        "updated_at": saved.get("updated_at"),
        "stale": stale,
    }


def _save_progress(job: Job, stage: str, detail: str) -> None:
    payload = json.loads(job.result_json or "{}")
    progress = payload.get("progress") if isinstance(payload.get("progress"), dict) else {}
    now = utcnow()
    started = job.started_at or now
    elapsed = max(0.0, (now - started).total_seconds())
    percent = _progress_value(stage, detail)
    progress.update({
        "stage": stage,
        "detail": detail[:500],
        "percent": percent,
        "updated_at": now.isoformat(),
        "elapsed_seconds": round(elapsed, 1),
        "eta_seconds": round(elapsed * (100 - percent) / percent, 1)
        if percent > 0 and elapsed >= 2 and percent < 100 else None,
    })
    payload["progress"] = progress
    job.result_json = json.dumps(payload, ensure_ascii=False)


class WorkerBusy(RuntimeError):
    pass


class CollectionBusy(RuntimeError):
    pass





class DuplicateWindowError(RuntimeError):
    """A window already has a built edition and no job that can be reused."""


@dataclass
class DueJob:
    job: Job
    cutoff: datetime
    release_at: datetime
    skipped: bool = False


def _weekdays(schedule: EditionSchedule) -> set[int]:
    return window_math.enabled_weekdays(schedule.weekdays)


def _clock(value: str, fallback: time) -> time:  # noqa: F821 - time imported below for typing
    return window_math.parse_clock(value, fallback)


def cutoff_for_day(schedule: EditionSchedule, day: date) -> datetime:
    return window_math.cutoff_for_day(schedule, day)


def release_for_day(schedule: EditionSchedule, day: date) -> datetime:
    return window_math.release_for_day(schedule, day)


def previous_enabled_day(schedule: EditionSchedule, day: date) -> date:
    return window_math.previous_enabled_day(schedule.weekdays, day)


def latest_due_cutoff(schedule: EditionSchedule, now: datetime | None = None) -> datetime | None:
    now = now or utcnow()
    local_today = to_local(now, schedule.timezone).date()
    enabled = _weekdays(schedule)
    for offset in range(0, 15):
        day = local_today - timedelta(days=offset)
        if day.weekday() not in enabled:
            continue
        cutoff = cutoff_for_day(schedule, day)
        if cutoff <= now:
            return cutoff
    return None


def due_cutoffs(
    db: Session,
    schedule: EditionSchedule,
    *,
    now: datetime | None = None,
    max_catchup: int = 3,
) -> list[datetime]:
    now = now or utcnow()
    watermark = db.scalar(
        select(EditionWatermark).where(
            EditionWatermark.channel_id == schedule.channel_id,
            EditionWatermark.kind == schedule.kind,
        )
    )
    to_enqueue, _skipped = window_math.due_cutoffs(
        schedule,
        watermark=watermark.last_cutoff_at if watermark else None,
        now=now,
        max_catchup=max_catchup,
    )
    return to_enqueue


def enqueue_due_jobs(
    db: Session,
    *,
    now: datetime | None = None,
    max_catchup: int = 3,
) -> list[Job]:
    now = now or utcnow()
    max_catchup = max(1, int(max_catchup))
    created: list[Job] = []
    for schedule in db.scalars(select(EditionSchedule).where(EditionSchedule.enabled.is_(True))):
        channel = db.get(Channel, schedule.channel_id)
        if channel is None:
            continue
        publication = (
            db.get(PublicationProfile, schedule.publication_id)
            if schedule.publication_id
            else default_publication(db)
        )
        sources = (
            publication_channels(db, publication, enabled_only=True)
            if publication is not None
            else []
        )
        if not sources:
            sources = [channel]
        source_ids = [item.id for item in sources]
        scope = f"pub{publication.id}:" if publication is not None else ""
        watermark = db.scalar(
            select(EditionWatermark).where(
                EditionWatermark.channel_id == schedule.channel_id,
                EditionWatermark.kind == schedule.kind,
            )
        )
        cutoffs, skipped = window_math.due_cutoffs(
            schedule,
            watermark=watermark.last_cutoff_at if watermark else None,
            now=now,
            max_catchup=max_catchup,
        )
        # Record every skipped date explicitly; a workstation that was off for
        # weeks must not leave an invisible gap in the record.
        for missed in skipped:
            missed_window = window_math.window_for_schedule(schedule, missed)
            if publication is not None:
                missed_window = replace(missed_window, publication_id=publication.id)
            get_or_create_window(
                db,
                missed_window,
                status="skipped",
                skip_reason=(
                    "Older than the configured catch-up limit; not built automatically. "
                    "Run a manual edition for this date if it is still needed."
                ),
            )
        for cutoff in cutoffs:
            window = window_math.window_for_schedule(schedule, cutoff)
            if publication is not None:
                window = replace(window, publication_id=publication.id)
            key = f"edition:{scope}{schedule.channel_id}:{schedule.kind}:{window.end.isoformat()}"
            existing = db.scalar(select(Job).where(Job.key == key))
            record = get_or_create_window(db, window)
            if existing is not None:
                if not record.job_id:
                    record.job_id = existing.id
                continue
            release_at = release_for_day(schedule, to_local(window.end, schedule.timezone).date())
            job = Job(
                key=key,
                kind="edition",
                channel_id=schedule.channel_id,
                publication_id=publication.id if publication is not None else None,
                status="scheduled",
                stage="queued",
                scheduled_for=cutoff,
                max_attempts=3,
                payload_json=json.dumps(
                    {
                        "schedule_id": schedule.id,
                        "publication_id": publication.id if publication is not None else None,
                        "channel_ids": source_ids,
                        "cutoff": cutoff.isoformat(),
                        "release_at": release_at.isoformat(),
                        "window_start": window.start.isoformat(),
                        "window_end": window.end.isoformat(),
                        "window_key": window.key,
                        "timezone": schedule.timezone,
                        "cutoff_local": schedule.cutoff_time,
                        "collect": any(not item.is_demo for item in sources),
                        "requires_review": bool(schedule.requires_review),
                        "auto_publish_nonrisk": bool(schedule.auto_publish_nonrisk),
                        "edition_kind": schedule.kind,
                    },
                    ensure_ascii=False,
                ),
            )
            db.add(job)
            db.flush()
            record.job_id = job.id
            record.status = "pending"
            created.append(job)
            audit(
                db,
                None,
                "job.enqueue",
                "job",
                job.id,
                {
                    "channel": schedule.channel_id,
                    "publication": publication.id if publication is not None else None,
                    "sources": source_ids,
                    "cutoff": cutoff.isoformat(),
                },
            )
        # NOTE: the committed watermark is intentionally *not* advanced here.
        # It moves only when a window is committed (released edition or a
        # verified zero-content window), see collection.service.commit_window.
    db.flush()
    return created


def enqueue_manual_job(
    db: Session,
    channel: Channel,
    *,
    window_start: datetime,
    window_end: datetime,
    kind: str = "manual",
    title: str = "",
    actor_id: int | None = None,
    release_at: datetime | None = None,
    collect: bool | None = None,
    timezone: str | None = None,
    cutoff_local: str = "05:00",
    publication: PublicationProfile | None = None,
    channels: list[Channel] | None = None,
) -> Job:
    """Queue exactly one durable job for a manual cutoff window.

    ``channels`` selects the source set for a multi-source newspaper build. When
    omitted, a publication-aware call uses that newspaper's enabled sources and
    a legacy call keeps the single-channel behaviour. The job key is
    deterministic (publication/channel + kind + exact half-open interval), so a
    double-click, a refreshed form or a retried POST cannot create a second job
    or a duplicate edition. A failed or cancelled job for the same window is
    re-armed in place instead of duplicated.
    """
    now = utcnow()
    tz_name = timezone or settings.timezone
    if channels:
        source_channels: list[Channel] = []
        seen: set[int] = set()
        for item in channels:
            if item.id not in seen:
                seen.add(item.id)
                source_channels.append(item)
    elif publication is not None:
        source_channels = publication_channels(db, publication, enabled_only=True)
    else:
        source_channels = []
    if not source_channels:
        source_channels = [channel]
    source_ids = [item.id for item in source_channels]
    scope = f"pub{publication.id}:" if publication is not None else ""
    window = EditionWindow(
        channel_id=channel.id,
        kind=kind,
        start=window_start,
        end=window_end,
        timezone=tz_name,
        cutoff_local=cutoff_local,
        publication_id=publication.id if publication is not None else None,
    )
    key = (
        f"manual:{scope}{channel.id}:{kind}:"
        f"{window.start.isoformat()}:{window.end.isoformat()}"
    )
    payload = json.dumps(
        {
            "manual": True,
            "actor_id": actor_id,
            "title": title,
            "publication_id": publication.id if publication is not None else None,
            "channel_ids": source_ids,
            "cutoff": window.end.isoformat(),
            "release_at": (release_at or now).isoformat(),
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
            "window_key": window.key,
            "timezone": tz_name,
            "cutoff_local": cutoff_local,
            "collect": (
                any(not item.is_demo for item in source_channels) if collect is None else bool(collect)
            ),
            # A manual build is always staged for editorial review. Only an
            # explicit release action may publish it.
            "requires_review": True,
            "auto_publish_nonrisk": False,
            "edition_kind": kind,
        },
        ensure_ascii=False,
    )
    record = db.scalar(
        select(EditionWindowRecord).where(EditionWindowRecord.window_key == window.key)
    )
    existing = db.scalar(select(Job).where(Job.key == key))
    if existing is None and record is not None and record.job_id:
        existing = db.get(Job, record.job_id)
    if existing is not None:
        if existing.status in JOB_RUNNABLE or existing.status in JOB_ALREADY_DONE or existing.status in {"running", "cancel_requested"}:
            existing.reused_existing = True  # type: ignore[attr-defined]
            return existing
        # failed_terminal / failed_retryable / canceled: reuse the same row so
        # the window keeps one job and one edition at most.
        existing.status = "scheduled"
        existing.stage = "queued"
        existing.attempt = 0
        existing.error = ""
        if not existing.edition_id:
            existing.result_json = "{}"
        existing.scheduled_for = now
        existing.started_at = None
        existing.finished_at = None
        existing.heartbeat_at = None
        existing.lock_owner = ""
        existing.lock_expires_at = None
        existing.channel_id = channel.id
        existing.publication_id = publication.id if publication is not None else None
        existing.payload_json = payload
        get_or_create_window(db, window, job_id=existing.id)
        db.flush()
        existing.requeued_existing = True  # type: ignore[attr-defined]
        audit(
            db,
            None,
            "job.manual_requeue",
            "job",
            existing.id,
            {"channel": channel.id, "sources": source_ids},
        )
        return existing
    if record is not None and (
        record.edition_id or record.status in {"committed", "awaiting_review"}
    ):
        raise DuplicateWindowError(
            f"This window was already built (edition #{record.edition_id}). "
            "Open that edition instead of queueing a duplicate."
        )
    job = Job(
        key=key,
        kind="edition",
        channel_id=channel.id,
        publication_id=publication.id if publication is not None else None,
        status="scheduled",
        stage="queued",
        scheduled_for=now,
        max_attempts=2,
        payload_json=payload,
    )
    db.add(job)
    db.flush()
    get_or_create_window(db, window, job_id=job.id)
    audit(
        db,
        None,
        "job.manual",
        "job",
        job.id,
        {"channel": channel.id, "publication": job.publication_id, "sources": source_ids},
    )
    return job


def enqueue_collect_job(
    db: Session,
    channel: Channel,
    *,
    window: EditionWindow,
    mode: str,
    actor_id: int | None = None,
    limit: int | None = None,
) -> Job:
    """Queue a collection-only job: ``sync_window`` or ``collect_context``."""
    if mode not in {"sync_window", "collect_context"}:
        raise ValueError("Unknown collection job mode.")
    key = f"{mode}:{window.key}"
    existing = db.scalar(select(Job).where(Job.key == key))
    if existing is not None:
        if existing.status in JOB_RUNNABLE | JOB_ALREADY_DONE | {"running", "cancel_requested"}:
            existing.reused_existing = True  # type: ignore[attr-defined]
            return existing
        # Failed or cancelled collection for the same window: re-arm this row
        # so repeated clicks keep exactly one job per window. The retrieval
        # checkpoint and staged pages let it resume instead of re-fetching.
        existing.status = "scheduled"
        existing.stage = "queued"
        existing.attempt = 0
        existing.error = ""
        existing.scheduled_for = utcnow()
        existing.started_at = None
        existing.finished_at = None
        existing.heartbeat_at = None
        existing.lock_owner = ""
        existing.lock_expires_at = None
        existing.channel_id = channel.id
        existing.requeued_existing = True  # type: ignore[attr-defined]
        audit(db, None, f"job.{mode}_requeue", "job", existing.id, {"window": window.key})
        return existing
    job = Job(
        key=key,
        kind=mode,
        channel_id=channel.id,
        status="scheduled",
        stage="queued",
        scheduled_for=utcnow(),
        max_attempts=2,
        payload_json=json.dumps(
            {
                "mode": mode,
                "actor_id": actor_id,
                "window_start": window.start.isoformat(),
                "window_end": window.end.isoformat(),
                "window_key": window.key,
                "timezone": window.timezone,
                "cutoff_local": window.cutoff_local,
                "edition_kind": window.kind,
                "limit": limit,
            },
            ensure_ascii=False,
        ),
    )
    db.add(job)
    db.flush()
    audit(db, None, f"job.{mode}", "job", job.id, {"channel": channel.id, "window": window.key})
    return job


def enqueue_republish_job(
    db: Session,
    edition: Edition,
    *,
    actor_id: int | None = None,
) -> Job:
    """Queue a versioned re-render of an existing edition's article set."""
    now = utcnow()
    resolved = publication_for_edition(db, edition)
    job = Job(
        key=f"republish:{edition.id}:{uuid.uuid4().hex[:8]}",
        kind="republish_edition",
        channel_id=edition.channel_id,
        publication_id=edition.publication_id or (resolved.id if resolved else None),
        edition_id=edition.id,
        status="scheduled",
        stage="queued",
        scheduled_for=now,
        max_attempts=1,
        payload_json=json.dumps({"edition_id": edition.id, "actor_id": actor_id}, ensure_ascii=False),
    )
    db.add(job)
    db.flush()
    audit(db, None, "edition.republish_request", "edition", edition.id, {"job": job.id})
    return job


def _abandoned(job: Job, now: datetime) -> bool:
    """True only when the recorded owner can no longer be making progress.

    A live local process keeps its lease even when a heartbeat was delayed, so
    this never steals from an active worker. A ticket owned by another host is
    only reclaimed after its explicit lease expires.
    """
    last = job.heartbeat_at or job.started_at or job.updated_at
    quiet = last is None or (
        (now - last).total_seconds() > settings.worker_heartbeat_timeout_seconds
    )
    alive = owner_process_alive(job.lock_owner)
    if alive is True:
        return False
    if alive is False:
        return quiet
    if not (job.lock_owner or "").strip():
        return quiet
    return job.lock_expires_at is not None and job.lock_expires_at < now


def release_expired_locks(db: Session, *, now: datetime | None = None) -> int:
    now = now or utcnow()
    lease_expired = list(
        db.scalars(
            select(Job).where(
                Job.status == "running",
                Job.lock_expires_at.is_not(None),
                Job.lock_expires_at < now,
            )
        )
    )
    # Never steal a lease from a local process that is provably still alive,
    # even if its heartbeat or lease renewal was delayed. Remote owners cannot
    # be probed, so their explicit lease expiry remains authoritative.
    stale = [job for job in lease_expired if owner_process_alive(job.lock_owner) is not True]
    # A crashed local worker must not block the desk for the full two-hour
    # lease. Only reclaim when the recorded process is provably gone.
    abandoned = [
        job
        for job in db.scalars(select(Job).where(Job.status == "running"))
        if _abandoned(job, now)
    ]
    stale.extend(job for job in abandoned if job not in stale)
    for job in stale:
        job.status = "failed_retryable" if job.attempt < job.max_attempts else "failed_terminal"
        job.error = "Worker lease expired before the job finished."
        job.lock_owner = ""
        job.lock_expires_at = None
        job.finished_at = now
        db.add(RetryAttempt(job_id=job.id, attempt=job.attempt, reason=job.error))
    # A cancel request whose worker died must not leave a zombie: finish the
    # cancellation, keeping the window and checkpoint available for a retry.
    for job in db.scalars(select(Job).where(Job.status == "cancel_requested")):
        if _abandoned(job, now):
            job.status = "canceled"
            job.stage = "canceled"
            job.error = "Cancelled after the worker stopped; no partial edition was released."
            job.lock_owner = ""
            job.lock_expires_at = None
            job.finished_at = now
            db.add(RetryAttempt(job_id=job.id, attempt=job.attempt, reason=job.error))
            stale.append(job)
    for job in db.scalars(select(Job).where(Job.status == "stop_requested")):
        if _abandoned(job, now):
            job.status = "paused"
            job.stage = "paused"
            job.error = "Stopped after the worker stopped; continue or restart the job."
            job.lock_owner = ""
            job.lock_expires_at = None
            job.finished_at = now
            db.add(RetryAttempt(job_id=job.id, attempt=job.attempt, reason=job.error))
            stale.append(job)
    db.flush()
    return len(stale)


def claim_job(
    db: Session,
    *,
    worker_id: str,
    now: datetime | None = None,
) -> Job | None:
    """Claim one runnable job with a compare-and-set update."""
    now = now or utcnow()
    candidates = list(
        db.scalars(
            select(Job)
            .where(
                Job.status.in_(JOB_RUNNABLE),
                Job.scheduled_for.is_not(None),
                Job.scheduled_for <= now,
                or_(Job.lock_expires_at.is_(None), Job.lock_expires_at < now),
            )
            .order_by(Job.scheduled_for.asc(), Job.id.asc())
            .limit(5)
        )
    )
    for job in candidates:
        result = db.execute(
            update(Job)
            .where(
                Job.id == job.id,
                Job.status.in_(JOB_RUNNABLE),
                or_(Job.lock_expires_at.is_(None), Job.lock_expires_at < now),
            )
            .values(
                status="running",
                stage="source_validation",
                lock_owner=worker_id,
                lock_expires_at=now + timedelta(minutes=settings.worker_lock_minutes),
                heartbeat_at=now,
                started_at=job.started_at or now,
                attempt=job.attempt + 1,
            )
        )
        if result.rowcount:
            db.flush()
            db.refresh(job)
            return job
    return None


def _job_steps(db: Session, job: Job, stages: list[tuple[str, str]]) -> None:
    for name, detail in stages:
        db.add(
            JobStep(
                job_id=job.id,
                name=name,
                status="done",
                detail=detail[:1000],
                started_at=job.started_at,
                finished_at=utcnow(),
            )
        )


def _has_blocking_flags(db: Session, edition_id: int) -> list[str]:
    return edition_review_blockers(db, edition_id)


def finalize_auto_publish(db: Session, edition: Edition, *, actor_id: int | None = None) -> bool:
    blockers = edition_review_blockers(db, edition.id)
    article_ids = list(
        db.scalars(select(EditionArticle.article_id).where(EditionArticle.edition_id == edition.id))
    )
    high_risk = bool(
        db.scalar(
            select(Article.id)
            .where(
                Article.id.in_(article_ids),
                or_(Article.risk_level == "high", Article.status == "needs_review"),
            )
            .limit(1)
        )
        if article_ids
        else False
    )
    if blockers or high_risk:
        return False
    edition.status = "published"
    edition.published_at = utcnow()
    from ..publishing.service import finalize_media_cleanup, record_manifest_release

    record_manifest_release(edition)
    commit_edition_window(db, edition)
    db.add(
        PublicationAttempt(
            edition_id=edition.id,
            destination=edition.output_dir or "local",
            status="succeeded",
            checksum="",
            completed_at=utcnow(),
        )
    )
    # Safe now: the PDF and e-paper bundle are already written and checked.
    finalize_media_cleanup(db, edition)
    audit(db, None, "edition.auto_publish", "edition", edition.id, {"actor": actor_id})
    db.flush()
    return True


def process_job(
    db: Session,
    job: Job,
    *,
    worker_id: str,
    progress: Callable[[str, str], None] | None = None,
) -> dict:
    db.info["durable_worker"] = True
    payload = json.loads(job.payload_json or "{}")
    if job.kind in {"sync_window", "collect_context"}:
        channel = db.get(Channel, job.channel_id) if job.channel_id else None
        if channel is None:
            raise PublicationError("Collection job has no valid channel.")
        window = EditionWindow(
            channel_id=channel.id,
            kind=str(payload.get("edition_kind") or "manual"),
            start=datetime.fromisoformat(payload["window_start"]),
            end=datetime.fromisoformat(payload["window_end"]),
            timezone=str(payload.get("timezone") or settings.timezone),
            cutoff_local=str(payload.get("cutoff_local") or settings.cutoff_local_time),
        )
        record = get_or_create_window(db, window, job_id=job.id)
        if not claim_window(db, record, owner=worker_id):
            raise CollectionBusy("Collection already active. Waiting for the window owner.")
        # Commit the claim before retrieval: the gateway thread checkpoints each
        # page through its own connection and must not be blocked by our lock.
        db.commit()
        job.stage = "collection"
        job.heartbeat_at = utcnow()
        db.commit()
        if progress:
            progress("collection", f"Collecting {window.label()}")
        rights = active_rights(db, channel.id)
        source = LiveTelegramSource(
            channel.handle or settings.telegram_source_username,
            limit=payload.get("limit"),
        )
        stats = collect_window(
            db,
            channel,
            window,
            source,
            rights=rights,
            actor_id=payload.get("actor_id"),
            job_id=job.id,
            record=record,
        )
        if job.kind == "collect_context":
            # Context-only backfill never commits a normal edition window.
            record.status = "context"
            record.committed_at = None
            record.stats_json = json.dumps(stats.as_dict(), ensure_ascii=False)
        job.status = "completed"
        job.stage = "collected"
        job.finished_at = utcnow()
        job.heartbeat_at = job.finished_at
        job.lock_owner = ""
        job.lock_expires_at = None
        job.result_json = json.dumps(
            {"collection": stats.as_dict(), "window": window.as_dict(), "mode": job.kind},
            ensure_ascii=False,
        )
        db.flush()
        return json.loads(job.result_json)

    if job.kind == "republish_edition":
        from ..editorial.revisions import working_revision
        from ..publishing.service import render_preview

        source = db.get(Edition, int(payload.get("edition_id") or job.edition_id or 0))
        if source is None:
            raise PublicationError("Republish job has no valid edition.")
        # A re-render never mutates a published edition. It creates (or reuses)
        # the next working version and renders a private preview from the latest
        # saved article revisions. Release stays a separate editorial action.
        edition = working_revision(db, source, actor_id=payload.get("actor_id"))
        job.edition_id = edition.id
        job.stage = "preview"
        job.heartbeat_at = utcnow()
        if progress:
            progress("preview", f"Rendering version {edition.version} preview")
        result = render_preview(db, edition, actor_id=payload.get("actor_id"))
        job.status = "awaiting_review"
        job.stage = "preview_ready"
        job.finished_at = utcnow()
        job.heartbeat_at = job.finished_at
        job.lock_owner = ""
        job.lock_expires_at = None
        job.result_json = json.dumps(
            {
                "edition_id": edition.id,
                "source_edition_id": source.id,
                "status": edition.status,
                "preview_dir": str(result.output_dir),
                "version": edition.version,
                "warnings": result.warnings,
            },
            ensure_ascii=False,
        )
        _job_steps(db, job, [("preview", f"Rendered version {edition.version} preview")])
        db.flush()
        return json.loads(job.result_json)

    channel = db.get(Channel, job.channel_id) if job.channel_id else None
    if channel is None:
        raise PublicationError("Job has no valid channel.")
    edition_kind = str(payload.get("edition_kind") or "daily")
    from ..pipeline import PipelineRequest

    resumed_edition = db.get(Edition, job.edition_id) if job.edition_id else None
    if job.publication_id:
        publication = db.get(PublicationProfile, job.publication_id)
    elif resumed_edition is not None:
        publication = publication_for_edition(db, resumed_edition)
    else:
        publication = default_publication(db)
    requested_ids = [int(value) for value in (payload.get("channel_ids") or []) if value]
    source_channels: list[Channel] = []
    if requested_ids:
        rows = {
            item.id: item
            for item in db.scalars(select(Channel).where(Channel.id.in_(requested_ids)))
        }
        source_channels = [rows[value] for value in requested_ids if value in rows]
    elif publication is not None:
        source_channels = publication_channels(db, publication, enabled_only=True)
    if not source_channels:
        source_channels = [channel]
    if channel.id not in {item.id for item in source_channels}:
        source_channels.insert(0, channel)

    stages: list[tuple[str, str]] = []
    collection_stats: dict = {}
    source_results: list[dict] = []
    failed_sources: set[int] = set()
    empty_sources: set[int] = set()
    window_start = datetime.fromisoformat(payload["window_start"])
    window_end = datetime.fromisoformat(payload["window_end"])

    def _source_window(item: Channel) -> EditionWindow:
        return EditionWindow(
            channel_id=item.id,
            kind=edition_kind,
            start=window_start,
            end=window_end,
            timezone=str(payload.get("timezone") or settings.timezone),
            cutoff_local=str(payload.get("cutoff_local") or settings.cutoff_local_time),
            publication_id=publication.id if publication is not None else None,
        )

    window = _source_window(channel)

    def on_progress(stage: str, detail: str) -> None:
        from ..operations import check_job_control
        check_job_control(job.id)
        job.stage = stage
        job.heartbeat_at = utcnow()
        _save_progress(job, stage, detail)
        stages.append((stage, detail or ""))
        if progress:
            progress(stage, detail)
        db.commit()

    want_collect = bool(payload.get("collect")) and not job.edition_id
    if want_collect:
        for index, item in enumerate(source_channels, start=1):
            if item.is_demo:
                source_results.append(
                    {
                        "channel_id": item.id,
                        "title": item.title,
                        "status": "fixture",
                        "message": "Synthetic fixture source; no Telegram retrieval.",
                    }
                )
                continue
            source_window = _source_window(item)
            try:
                record = get_or_create_window(db, source_window, job_id=job.id)
                previous = db.scalar(
                    select(EditionWindowRecord)
                    .where(
                        EditionWindowRecord.window_key == source_window.key,
                        EditionWindowRecord.status.in_(
                            ["collected", "built", "awaiting_review", "committed"]
                        ),
                    )
                    .order_by(EditionWindowRecord.id.desc())
                )
                saved = json.loads(previous.stats_json or "{}") if previous else {}
                if saved.get("complete"):
                    if active_rights(db, item.id) is None:
                        raise PublicationError("Source authorization missing.")
                    stats = CollectionStats(**saved)
                    record.status = "collected"
                    record.stats_json = json.dumps(saved)
                    reused = True
                else:
                    if not claim_window(db, record, owner=worker_id):
                        raise CollectionBusy(
                            "Collection already active. Waiting for the window owner."
                        )
                    db.commit()
                    on_progress(
                        "collection",
                        f"Source {index}/{len(source_channels)}: retrieving {item.title}",
                    )
                    db.commit()
                    rights = active_rights(db, item.id)
                    source = LiveTelegramSource(item.handle or settings.telegram_source_username)
                    stats = collect_window(
                        db,
                        item,
                        source_window,
                        source,
                        rights=rights,
                        actor_id=payload.get("actor_id"),
                        job_id=job.id,
                        record=record,
                    )
                    reused = False
                stats_dict = stats.as_dict()
                collection_stats[item.id] = stats_dict
                db.flush()
                if stats.fetched == 0 or stats.included == 0:
                    empty_sources.add(item.id)
                    if not reused:
                        mark_no_content(db, record, stats)
                    source_results.append(
                        {
                            "channel_id": item.id,
                            "title": item.title,
                            "status": "no_content",
                            "message": (
                                "No includable source posts were published in this window."
                                if stats.fetched
                                else "The source channel published nothing in this window."
                            ),
                            "collection": stats_dict,
                        }
                    )
                else:
                    source_results.append(
                        {
                            "channel_id": item.id,
                            "title": item.title,
                            "status": "collected",
                            "message": "Collection complete.",
                            "collection": stats_dict,
                        }
                    )
                job.result_json = json.dumps(
                    {"collection": collection_stats, "sources": source_results},
                    ensure_ascii=False,
                )
                db.commit()
            except CollectionBusy:
                raise
            except Exception as exc:  # one source must not silently vanish
                failed_sources.add(item.id)
                source_results.append(
                    {
                        "channel_id": item.id,
                        "title": item.title,
                        "status": "failed",
                        "message": str(exc)[:400],
                    }
                )
                logger.warning(
                    "source_collection_failed",
                    extra={"job_id": job.id, "channel_id": item.id},
                    exc_info=True,
                )
                on_progress(
                    "collection",
                    f"Source {index}/{len(source_channels)}: {item.title} failed; continuing with collected sources",
                )

    available_sources = [item for item in source_channels if item.id not in failed_sources]
    if not available_sources:
        detail = "; ".join(
            f"{row['title']}: {row['message']}"
            for row in source_results
            if row.get("status") == "failed"
        )
        raise PublicationError(
            "No source channel could be collected for this edition. " + detail
        )
    if want_collect and all(item.id in empty_sources for item in available_sources):
        aggregate = CollectionStats()
        for payload_stats in collection_stats.values():
            for key in (
                "fetched",
                "included",
                "excluded",
                "duplicates",
                "replies",
                "albums",
                "media",
                "created",
                "updated",
                "pages",
            ):
                setattr(aggregate, key, getattr(aggregate, key) + int(payload_stats.get(key) or 0))
            aggregate.errors.extend(payload_stats.get("errors") or [])
            aggregate.notes.extend(payload_stats.get("notes") or [])
        mark_no_content(db, get_or_create_window(db, window, job_id=job.id), aggregate)
        job.status = "no_content"
        job.stage = "completed"
        job.finished_at = utcnow()
        job.heartbeat_at = job.finished_at
        job.lock_owner = ""
        job.lock_expires_at = None
        job.result_json = json.dumps(
            {
                "collection": collection_stats,
                "sources": source_results,
                "status": "no_content",
                "message": "No includable source posts were published in this window.",
            },
            ensure_ascii=False,
        )
        _job_steps(db, job, stages + [("collection", "Verified empty window")])
        db.flush()
        return json.loads(job.result_json)

    request = PipelineRequest(
        channel=channel,
        channels=available_sources,
        publication=publication,
        window_start=window_start,
        window_end=window_end,
        cutoff_at=datetime.fromisoformat(payload.get("cutoff") or payload["window_end"]),
        target_release_at=datetime.fromisoformat(payload.get("release_at") or payload["window_end"]),
        kind=edition_kind,
        title=str(payload.get("title") or ""),
        actor_id=payload.get("actor_id"),
        job_id=job.id,
        publish=True,
        mark_published=False,
        progress=on_progress,
    )
    result: PipelineResult = run_pipeline(db, request)
    edition = result.edition
    partial_sources = bool(failed_sources or empty_sources) or (
        want_collect and len(available_sources) < len(source_channels)
    )
    edition.source_status = "partial" if partial_sources else "complete"
    requires_review = bool(payload.get("requires_review", True))
    auto_ok = False
    if not requires_review:
        auto_ok = finalize_auto_publish(db, edition, actor_id=payload.get("actor_id"))
    elif payload.get("auto_publish_nonrisk"):
        auto_ok = finalize_auto_publish(db, edition, actor_id=payload.get("actor_id"))
    if auto_ok:
        job.status = "published"
        job.stage = "published"
    else:
        job.status = "awaiting_review"
        job.stage = "review"
        mark_window_built(db, edition, status="awaiting_review")
    if record_for_edition(db, edition) is None:
        # Manual/one-off editions still get a window record for traceability.
        get_or_create_window(
            db,
            window,
            job_id=job.id,
            status="committed" if auto_ok else "awaiting_review",
        )
        if not auto_ok:
            mark_window_built(db, edition, status="awaiting_review")
    job.edition_id = edition.id
    job.finished_at = utcnow()
    job.heartbeat_at = job.finished_at
    job.lock_owner = ""
    job.lock_expires_at = None
    job.result_json = json.dumps(
        {
            "edition_id": edition.id,
            "status": edition.status,
            "output_dir": edition.output_dir,
            "collection": collection_stats,
            "sources": source_results,
            "source_status": edition.source_status,
            "source_channel_ids": json.loads(edition.source_channel_ids_json or "[]"),
            "pages": (result.publish_result and result.publish_result.warnings is not None)
            and json.loads(edition.manifest_json or "{}").get("pdf", {}).get("page_count"),
            "stories": result.stats.get("stories"),
            "articles": result.stats.get("articles"),
            "needs_review": result.stats.get("needs_review"),
            "provider": result.provider,
            "provider_note": result.provider_note,
            "duration_seconds": result.duration_seconds,
            "flags": result.flags[:20],
        },
        ensure_ascii=False,
    )
    _job_steps(db, job, stages + [("publishing", f"Edition {edition.id}: {edition.status}")])
    db.flush()
    return json.loads(job.result_json)


def _heartbeat_loop(
    job_id: int,
    stop_event: threading.Event,
    progress: Callable[[str, str], None] | None,
) -> None:
    """Keep long-running work observable without sharing the worker Session."""
    interval = max(5.0, min(15.0, settings.worker_heartbeat_timeout_seconds / 3))
    while not stop_event.wait(interval):
        try:
            with SessionLocal() as heartbeat_db:
                current = heartbeat_db.get(Job, job_id)
                if current is None or current.status != "running":
                    return
                stage = current.stage or "working"
                detail = f"Still working in {stage.replace('_', ' ')}…"
                current.heartbeat_at = utcnow()
                _save_progress(current, stage, detail)
                heartbeat_db.commit()
            if progress:
                progress(stage, detail)
        except Exception:
            logger.exception("worker_heartbeat_failed", extra={"job_id": job_id})


def fail_job(db: Session, job: Job, error: str, *, retryable: bool | None = None) -> None:
    if retryable is None:
        retryable = job.attempt < job.max_attempts
    job.status = "failed_retryable" if retryable else "failed_terminal"
    job.error = error[:4000]
    job.finished_at = utcnow()
    job.lock_owner = ""
    job.lock_expires_at = None
    if retryable:
        job.scheduled_for = utcnow() + timedelta(minutes=5 * max(1, job.attempt))
    db.add(RetryAttempt(job_id=job.id, attempt=job.attempt, reason=error[:800]))
    db.flush()


def release_due_editions(db: Session, *, now: datetime | None = None) -> list[int]:
    """Publish reviewed editions whose release time has passed and are clean."""
    now = now or utcnow()
    released: list[int] = []
    editions = list(
        db.scalars(
            select(Edition).where(
                Edition.status == "awaiting_review",
                Edition.target_release_at <= now,
            )
        )
    )
    for edition in editions:
        schedule = db.scalar(
            select(EditionSchedule).where(
                EditionSchedule.channel_id == edition.channel_id,
                EditionSchedule.kind == edition.kind,
            )
        )
        if schedule is None or schedule.requires_review:
            continue
        if not (schedule.auto_publish_nonrisk or not schedule.requires_review):
            continue
        if finalize_auto_publish(db, edition, actor_id=None):
            released.append(edition.id)
    db.flush()
    return released


class WorkerLease:
    """Single-worker lease implemented with an OS file lock.

    Windows uses ``msvcrt.locking``; POSIX uses ``fcntl.flock``. The lock is
    released automatically when the process exits, including a crash.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or (settings.data_dir / "worker.lock")
        self.handle = None

    def __enter__(self) -> "WorkerLease":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise WorkerBusy("Another Channel Press worker is already running.") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f"{os.getpid()}\n")
        self.handle.flush()
        return self

    def __exit__(self, *exc) -> None:  # type: ignore[no-untyped-def]
        if not self.handle:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


@dataclass
class WorkerStats:
    enqueued: int = 0
    processed: int = 0
    published: int = 0
    awaiting_review: int = 0
    failed: int = 0
    released: int = 0
    stale_locks: int = 0
    collected: int = 0
    no_content: int = 0


def _worker_once(
    db: Session,
    *,
    now: datetime | None = None,
    max_jobs: int = 5,
    progress: Callable[[str, str], None] | None = None,
) -> WorkerStats:
    now = now or utcnow()
    stats = WorkerStats()
    stats.stale_locks = release_expired_locks(db, now=now)
    stats.enqueued = len(
        enqueue_due_jobs(db, now=now, max_catchup=settings.max_catchup_windows)
    )
    stats.released = len(release_due_editions(db, now=now))
    db.commit()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    for _ in range(max_jobs):
        job = claim_job(db, worker_id=worker_id, now=utcnow() + timedelta(seconds=1))
        if job is None:
            db.commit()
            break
        db.commit()
        heartbeat_stop = threading.Event()
        heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            args=(job.id, heartbeat_stop, progress),
            name=f"job-heartbeat-{job.id}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            outcome = process_job(db, job, worker_id=worker_id, progress=progress)
            db.commit()
            stats.processed += 1
            if outcome.get("status") == "published":
                stats.published += 1
            elif outcome.get("status") == "no_content":
                stats.no_content += 1
            elif job.kind in {"sync_window", "collect_context"}:
                stats.collected += 1
            else:
                stats.awaiting_review += 1
        except Exception as exc:
            db.rollback()
            fresh = db.get(Job, job.id)
            if isinstance(exc, CollectionBusy):
                fresh.status = "scheduled"
                fresh.stage = "waiting_for_collector"
                fresh.error = str(exc)
                fresh.attempt = max(0, fresh.attempt - 1)
                fresh.scheduled_for = utcnow() + timedelta(seconds=30)
                fresh.lock_owner = ""
                fresh.lock_expires_at = None
                db.commit()
                continue
            from ..operations import JobCanceled, StopRequested
            if isinstance(exc, StopRequested):
                fresh.status = "canceled" if isinstance(exc, JobCanceled) else "paused"
                fresh.error = str(exc)
                fresh.lock_owner = ""
                fresh.lock_expires_at = None
                fresh.attempt = max(0, fresh.attempt - 1)
                for row in db.scalars(select(EditionWindowRecord).where(EditionWindowRecord.lock_owner == worker_id)):
                    row.lock_owner = ""
                    row.lock_expires_at = None
                    row.status = "failed"
                db.commit()
                break
            logger.error(
                "job_failed",
                extra={
                    "event": "job_failed",
                    "job_id": job.id,
                    "kind": job.kind,
                    "channel_id": job.channel_id,
                },
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            if fresh is not None:
                detail = f"{type(exc).__name__}: {str(exc)[:1800]}"
                frames = traceback.extract_tb(exc.__traceback__)
                if frames:
                    last = frames[-1]
                    detail += f" [at {Path(last.filename).name}:{last.lineno} in {last.name}]"
                # A deterministic pipeline/rights/publication refusal will not
                # fix itself in five minutes; only unexpected errors retry.
                retryable = (not isinstance(exc, (PipelineError, PublicationError))
                             and fresh.attempt < fresh.max_attempts)
                # Release the persisted collection lease on failure; keep the
                # retrieval cursor and staged pages for an exact-window retry.
                for window_row in db.scalars(select(EditionWindowRecord).where(
                    EditionWindowRecord.lock_owner == worker_id
                )):
                    window_row.lock_owner = ""
                    window_row.lock_expires_at = None
                    window_row.status = "failed"
                fail_job(db, fresh, detail, retryable=retryable)
                audit(db, None, "job.failed", "job", fresh.id, {"error": str(exc)[:500]})
                db.commit()
            stats.failed += 1
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)
    return stats


def worker_once(db: Session, **kwargs) -> WorkerStats:
    """All entry points share the same single OS ownership gate.

    ``cli worker``, the detached web launch and the scheduled task all arrive
    here, so a double click or a Task Scheduler overlap can never create two
    competing collectors. A second caller gets ``WorkerBusy`` and simply
    waits for the next pass.
    """
    from ..operations import check_stop, managed_service

    check_stop()
    with WorkerLease(), managed_service("worker"):
        return _worker_once(db, **kwargs)
