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
import socket
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from ..auth import audit
from ..config import settings
from ..models import (
    Article,
    Channel,
    Edition,
    EditionArticle,
    EditionSchedule,
    EditionWatermark,
    Job,
    JobStep,
    PublicationAttempt,
    RetryAttempt,
    ReviewFlag,
)
from ..pipeline import PipelineResult, run_pipeline
from ..timeutil import local_boundary_utc, to_local, utcnow
from ..publishing.service import PublicationError, edition_review_blockers

JOB_RUNNABLE = {"scheduled", "failed_retryable", "resumable"}


class WorkerBusy(RuntimeError):
    pass


@dataclass
class DueJob:
    job: Job
    cutoff: datetime
    release_at: datetime
    skipped: bool = False


def _weekdays(schedule: EditionSchedule) -> set[int]:
    result: set[int] = set()
    for part in (schedule.weekdays or "").split(","):
        try:
            value = int(part.strip())
        except ValueError:
            continue
        if 0 <= value <= 6:
            result.add(value)
    return result or {0, 1, 2, 3, 4, 5, 6}


def _clock(value: str, fallback: time) -> time:
    try:
        hour, minute = value.split(":", 1)
        return time(int(hour), int(minute))
    except (ValueError, AttributeError):
        return fallback


def cutoff_for_day(schedule: EditionSchedule, day: date) -> datetime:
    return local_boundary_utc(day, _clock(schedule.cutoff_time, time(5, 0)), schedule.timezone)


def release_for_day(schedule: EditionSchedule, day: date) -> datetime:
    return local_boundary_utc(day, _clock(schedule.release_time, time(8, 0)), schedule.timezone)


def previous_enabled_day(schedule: EditionSchedule, day: date) -> date:
    enabled = _weekdays(schedule)
    cursor = day - timedelta(days=1)
    for _ in range(14):
        if cursor.weekday() in enabled:
            return cursor
        cursor -= timedelta(days=1)
    return cursor


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
    earliest = watermark.last_cutoff_at if watermark else None
    local_today = to_local(now, schedule.timezone).date()
    candidates: list[datetime] = []
    enabled = _weekdays(schedule)
    for offset in range(0, 31):
        day = local_today - timedelta(days=offset)
        if day.weekday() not in enabled:
            continue
        cutoff = cutoff_for_day(schedule, day)
        if cutoff > now:
            continue
        if earliest is not None and cutoff <= earliest:
            if offset > 0:
                break
            continue
        candidates.append(cutoff)
    candidates.sort()
    if not candidates:
        return []
    return candidates[-max_catchup:]


def enqueue_due_jobs(
    db: Session,
    *,
    now: datetime | None = None,
    max_catchup: int = 3,
) -> list[Job]:
    now = now or utcnow()
    created: list[Job] = []
    for schedule in db.scalars(select(EditionSchedule).where(EditionSchedule.enabled.is_(True))):
        channel = db.get(Channel, schedule.channel_id)
        if channel is None:
            continue
        cutoffs = due_cutoffs(db, schedule, now=now, max_catchup=max_catchup)
        if not cutoffs:
            continue
        for cutoff in cutoffs:
            key = f"edition:{schedule.channel_id}:{schedule.kind}:{cutoff.isoformat()}"
            existing = db.scalar(select(Job).where(Job.key == key))
            if existing is not None:
                continue
            window_start = cutoff_for_day(
                schedule, previous_enabled_day(schedule, to_local(cutoff, schedule.timezone).date())
            )
            release_at = release_for_day(schedule, to_local(cutoff, schedule.timezone).date())
            job = Job(
                key=key,
                kind="edition",
                channel_id=schedule.channel_id,
                status="scheduled",
                stage="queued",
                scheduled_for=cutoff,
                max_attempts=3,
                payload_json=json.dumps(
                    {
                        "schedule_id": schedule.id,
                        "cutoff": cutoff.isoformat(),
                        "release_at": release_at.isoformat(),
                        "window_start": window_start.isoformat(),
                        "window_end": cutoff.isoformat(),
                        "requires_review": bool(schedule.requires_review),
                        "auto_publish_nonrisk": bool(schedule.auto_publish_nonrisk),
                        "edition_kind": schedule.kind,
                    },
                    ensure_ascii=False,
                ),
            )
            db.add(job)
            db.flush()
            created.append(job)
            audit(
                db,
                None,
                "job.enqueue",
                "job",
                job.id,
                {"channel": schedule.channel_id, "cutoff": cutoff.isoformat()},
            )
        # Advance the watermark to the newest cutoff that now has a job. This
        # is safe because the unique job key remains the idempotency record.
        newest = max(cutoffs)
        watermark = db.scalar(
            select(EditionWatermark).where(
                EditionWatermark.channel_id == schedule.channel_id,
                EditionWatermark.kind == schedule.kind,
            )
        )
        if watermark is None:
            db.add(
                EditionWatermark(
                    channel_id=schedule.channel_id,
                    kind=schedule.kind,
                    last_cutoff_at=newest,
                )
            )
        elif watermark.last_cutoff_at < newest:
            watermark.last_cutoff_at = newest
            watermark.updated_at = utcnow()
    db.flush()
    return created


def enqueue_manual_job(
    db: Session,
    channel: Channel,
    *,
    window_start: datetime,
    window_end: datetime,
    kind: str = "manual",
    actor_id: int | None = None,
    release_at: datetime | None = None,
) -> Job:
    now = utcnow()
    key = f"manual:{channel.id}:{kind}:{window_start.isoformat()}:{window_end.isoformat()}:{uuid.uuid4().hex[:8]}"
    job = Job(
        key=key,
        kind="edition",
        channel_id=channel.id,
        status="scheduled",
        stage="queued",
        scheduled_for=now,
        max_attempts=1,
        payload_json=json.dumps(
            {
                "manual": True,
                "actor_id": actor_id,
                "cutoff": window_end.isoformat(),
                "release_at": (release_at or now).isoformat(),
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "requires_review": False,
                "auto_publish_nonrisk": False,
                "edition_kind": kind,
            },
            ensure_ascii=False,
        ),
    )
    db.add(job)
    db.flush()
    audit(db, None, "job.manual", "job", job.id, {"channel": channel.id})
    return job


def enqueue_republish_job(
    db: Session,
    edition: Edition,
    *,
    actor_id: int | None = None,
) -> Job:
    """Queue a versioned re-render of an existing edition's article set."""
    now = utcnow()
    job = Job(
        key=f"republish:{edition.id}:{uuid.uuid4().hex[:8]}",
        kind="republish_edition",
        channel_id=edition.channel_id,
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


def release_expired_locks(db: Session, *, now: datetime | None = None) -> int:
    now = now or utcnow()
    stale = list(
        db.scalars(
            select(Job).where(
                Job.status == "running",
                Job.lock_expires_at.is_not(None),
                Job.lock_expires_at < now,
            )
        )
    )
    for job in stale:
        job.status = "failed_retryable" if job.attempt < job.max_attempts else "failed_terminal"
        job.error = "Worker lease expired before the job finished."
        job.lock_owner = ""
        job.lock_expires_at = None
        job.finished_at = now
        db.add(RetryAttempt(job_id=job.id, attempt=job.attempt, reason=job.error))
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
    from ..publishing.service import record_manifest_release

    record_manifest_release(edition)
    db.add(
        PublicationAttempt(
            edition_id=edition.id,
            destination=edition.output_dir or "local",
            status="succeeded",
            checksum="",
            completed_at=utcnow(),
        )
    )
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
    payload = json.loads(job.payload_json or "{}")
    if job.kind == "republish_edition":
        from ..publishing.service import publish_edition

        edition = db.get(Edition, int(payload.get("edition_id") or job.edition_id or 0))
        if edition is None:
            raise PublicationError("Republish job has no valid edition.")
        job.stage = "layout"
        job.heartbeat_at = utcnow()
        if progress:
            progress("layout", f"Re-rendering edition {edition.id}")
        edition.version += 1
        edition.correction_note = edition.correction_note or "Re-rendered edition."
        result = publish_edition(
            db,
            edition,
            mark_published=True,
            actor_id=payload.get("actor_id"),
        )
        job.status = "published"
        job.stage = "published"
        job.finished_at = utcnow()
        job.heartbeat_at = job.finished_at
        job.lock_owner = ""
        job.lock_expires_at = None
        job.result_json = json.dumps(
            {
                "edition_id": edition.id,
                "status": edition.status,
                "output_dir": edition.output_dir,
                "pages": result.warnings and json.loads(edition.manifest_json or "{}").get("pdf", {}).get("page_count"),
                "version": edition.version,
            },
            ensure_ascii=False,
        )
        _job_steps(db, job, [("layout", f"Rendered version {edition.version}")])
        db.flush()
        return json.loads(job.result_json)

    channel = db.get(Channel, job.channel_id) if job.channel_id else None
    if channel is None:
        raise PublicationError("Job has no valid channel.")
    edition_kind = str(payload.get("edition_kind") or "daily")
    from ..pipeline import PipelineRequest

    stages: list[tuple[str, str]] = []

    def on_progress(stage: str, detail: str) -> None:
        job.stage = stage
        job.heartbeat_at = utcnow()
        stages.append((stage, detail or ""))
        if progress:
            progress(stage, detail)

    request = PipelineRequest(
        channel=channel,
        window_start=datetime.fromisoformat(payload["window_start"]),
        window_end=datetime.fromisoformat(payload["window_end"]),
        cutoff_at=datetime.fromisoformat(payload.get("cutoff") or payload["window_end"]),
        target_release_at=datetime.fromisoformat(payload.get("release_at") or payload["window_end"]),
        kind=edition_kind,
        actor_id=payload.get("actor_id"),
        job_id=job.id,
        publish=True,
        mark_published=False,
        progress=on_progress,
    )
    result: PipelineResult = run_pipeline(db, request)
    edition = result.edition
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


def worker_once(
    db: Session,
    *,
    now: datetime | None = None,
    max_jobs: int = 5,
    progress: Callable[[str, str], None] | None = None,
) -> WorkerStats:
    now = now or utcnow()
    stats = WorkerStats()
    stats.stale_locks = release_expired_locks(db, now=now)
    stats.enqueued = len(enqueue_due_jobs(db, now=now))
    stats.released = len(release_due_editions(db, now=now))
    db.commit()
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    for _ in range(max_jobs):
        job = claim_job(db, worker_id=worker_id, now=utcnow() + timedelta(seconds=1))
        if job is None:
            db.commit()
            break
        db.commit()
        try:
            outcome = process_job(db, job, worker_id=worker_id, progress=progress)
            db.commit()
            stats.processed += 1
            if outcome.get("status") == "published":
                stats.published += 1
            else:
                stats.awaiting_review += 1
        except Exception as exc:
            db.rollback()
            fresh = db.get(Job, job.id)
            if fresh is not None:
                fail_job(db, fresh, f"{type(exc).__name__}: {str(exc)[:1800]}")
                audit(db, None, "job.failed", "job", fresh.id, {"error": str(exc)[:500]})
                db.commit()
            stats.failed += 1
    return stats
