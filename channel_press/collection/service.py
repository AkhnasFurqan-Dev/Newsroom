"""Cutoff-to-cutoff collection with durable windows and checkpoints.

The flow for one scheduled edition:

1. A window row is created from the schedule (start inclusive, end exclusive).
2. Retrieval is claimed with a lease so two workers cannot collect it at once.
3. Messages are fetched page by page; the retrieval checkpoint records progress.
4. Messages are upserted by ``(channel_id, message_id)``, so a retry cannot
   duplicate posts, media or replies.
5. The window becomes ``collected``. Only a built-and-approved edition (or a
   verified zero-content window) commits the window and moves the watermark.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Protocol

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..auth import audit
from ..config import settings
from ..ingestion.service import import_payload
from ..models import (
    Channel,
    Edition,
    EditionWatermark,
    EditionWindowRecord,
    RetrievalCheckpoint,
    RightsGrant,
    SourcePost,
)
from ..timeutil import as_utc, parse_datetime, utcnow
from .window import EditionWindow, window_for_schedule


class CollectionError(RuntimeError):
    pass


class RightsGateError(CollectionError):
    pass


@dataclass
class CollectionStats:
    fetched: int = 0
    included: int = 0
    excluded: int = 0
    duplicates: int = 0
    replies: int = 0
    albums: int = 0
    media: int = 0
    created: int = 0
    updated: int = 0
    pages: int = 0
    errors: list[str] = field(default_factory=list)
    complete: bool = True
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "fetched": self.fetched,
            "included": self.included,
            "excluded": self.excluded,
            "duplicates": self.duplicates,
            "replies": self.replies,
            "albums": self.albums,
            "media": self.media,
            "created": self.created,
            "updated": self.updated,
            "pages": self.pages,
            "errors": self.errors,
            "complete": self.complete,
            "notes": self.notes,
        }


@dataclass
class FetchedPage:
    page: int
    messages: list[dict]
    oldest_id: int | None


@dataclass
class SourceResult:
    channel: dict
    messages: list[dict]
    pages: int = 0
    complete: bool = True
    resumed_from: int = 0
    notes: list[str] = field(default_factory=list)
    flood_waits: int = 0


class WindowSource(Protocol):
    name: str

    def fetch_window(
        self,
        window: EditionWindow,
        *,
        on_page: Callable[[FetchedPage], None] | None = None,
        resume_offset: int = 0,
    ) -> SourceResult: ...


class FixtureWindowSource:
    """Deterministic source used by tests and the demo; never claims to be live."""

    name = "fixture"

    def __init__(self, messages: list[dict], channel: dict | None = None):
        self._messages = messages
        self._channel = channel or {}

    def fetch_window(
        self,
        window: EditionWindow,
        *,
        on_page: Callable[[FetchedPage], None] | None = None,
        resume_offset: int = 0,
    ) -> SourceResult:
        selected = []
        for raw in self._messages:
            posted = parse_datetime(raw.get("date"))
            if posted is None or not window.contains(posted):
                continue
            if resume_offset and int(raw.get("id", 0)) >= resume_offset:
                continue
            selected.append(raw)
        selected.sort(key=lambda item: int(item.get("id", 0)))
        if on_page is not None:
            on_page(
                FetchedPage(
                    page=1,
                    messages=selected,
                    oldest_id=int(selected[0]["id"]) if selected else None,
                )
            )
        return SourceResult(channel=self._channel, messages=selected, pages=1, complete=True)


class LiveTelegramSource:
    """Authorized live retrieval through the shared Telegram gateway."""

    name = "telegram"

    def __init__(self, handle: str, service=None, limit: int | None = None):  # noqa: ANN001 - optional gateway
        from ..telegram.service import telegram_service

        self.handle = handle
        self.service = service or telegram_service
        self.limit = int(limit) if limit else None

    def fetch_window(
        self,
        window: EditionWindow,
        *,
        on_page: Callable[[FetchedPage], None] | None = None,
        resume_offset: int = 0,
    ) -> SourceResult:
        def _callback(page_no: int, messages: list[dict], oldest_id: int | None) -> None:
            if on_page is not None:
                on_page(FetchedPage(page=page_no, messages=messages, oldest_id=oldest_id))

        outcome = self.service.fetch_window(
            self.handle,
            window.start,
            window.end,
            limit=self.limit,
            resume_offset=resume_offset,
            on_page=_callback,
        )
        return SourceResult(
            channel=outcome.channel,
            messages=outcome.messages,
            pages=outcome.pages,
            complete=outcome.complete,
            resumed_from=outcome.resumed_from,
            notes=list(outcome.notes),
            flood_waits=outcome.flood_waits,
        )


# ---------------------------------------------------------------------------
# window bookkeeping
# ---------------------------------------------------------------------------


def get_or_create_window(
    db: Session,
    window: EditionWindow,
    *,
    job_id: int | None = None,
    status: str = "pending",
    skip_reason: str = "",
) -> EditionWindowRecord:
    record = db.scalar(
        select(EditionWindowRecord).where(EditionWindowRecord.window_key == window.key)
    )
    if record is None:
        record = EditionWindowRecord(
            window_key=window.key,
            channel_id=window.channel_id,
            kind=window.kind,
            window_start=window.start,
            window_end=window.end,
            timezone=window.timezone,
            cutoff_local=window.cutoff_local,
            status=status,
            skip_reason=skip_reason,
            job_id=job_id,
        )
        db.add(record)
        db.flush()
    elif job_id and not record.job_id:
        record.job_id = job_id
    return record


def window_from_record(record: EditionWindowRecord) -> EditionWindow:
    return EditionWindow(
        channel_id=record.channel_id,
        kind=record.kind,
        start=record.window_start,
        end=record.window_end,
        timezone=record.timezone,
        cutoff_local=record.cutoff_local,
    )


def claim_window(
    db: Session,
    record: EditionWindowRecord,
    *,
    owner: str,
    lease_minutes: int = 60,
    now: datetime | None = None,
) -> bool:
    """Compare-and-set claim. Returns False when another worker owns the window."""
    now = now or utcnow()
    from sqlalchemy import update

    result = db.execute(
        update(EditionWindowRecord)
        .where(
            EditionWindowRecord.id == record.id,
            EditionWindowRecord.status.in_(
                ["pending", "collecting", "collected", "failed"]
            ),
            (EditionWindowRecord.lock_expires_at.is_(None))
            | (EditionWindowRecord.lock_expires_at < now),
        )
        .values(
            status="collecting",
            lock_owner=owner,
            lock_expires_at=now + timedelta(minutes=lease_minutes),
            updated_at=now,
        )
    )
    db.flush()
    if result.rowcount:
        db.refresh(record)
        return True
    return False


def release_window(db: Session, record: EditionWindowRecord, *, status: str | None = None) -> None:
    record.lock_owner = ""
    record.lock_expires_at = None
    if status:
        record.status = status
    record.updated_at = utcnow()
    db.flush()


def save_checkpoint(
    db: Session,
    window: EditionWindow,
    *,
    adapter: str,
    page: int,
    oldest_id: int | None,
    fetched: int,
    status: str = "in_progress",
    error: str = "",
) -> RetrievalCheckpoint:
    checkpoint = db.scalar(
        select(RetrievalCheckpoint).where(RetrievalCheckpoint.window_key == window.key)
    )
    if checkpoint is None:
        checkpoint = RetrievalCheckpoint(
            window_key=window.key,
            channel_id=window.channel_id,
            adapter=adapter,
        )
        db.add(checkpoint)
    checkpoint.pages = max(checkpoint.pages or 0, int(page or 0))
    if oldest_id is not None:
        checkpoint.cursor_message_id = int(oldest_id)
    checkpoint.fetched_count = int(fetched or 0)
    checkpoint.status = status
    checkpoint.last_error = error[:2000]
    checkpoint.updated_at = utcnow()
    db.flush()
    return checkpoint


def checkpoint_for(db: Session, window: EditionWindow) -> RetrievalCheckpoint | None:
    return db.scalar(
        select(RetrievalCheckpoint).where(RetrievalCheckpoint.window_key == window.key)
    )


def _watermark(db: Session, channel_id: int, kind: str) -> EditionWatermark | None:
    return db.scalar(
        select(EditionWatermark).where(
            EditionWatermark.channel_id == channel_id,
            EditionWatermark.kind == kind,
        )
    )


def recompute_watermark(db: Session, channel_id: int, kind: str) -> datetime | None:
    """Advance the committed watermark only across contiguous committed windows.

    A gap (an uncommitted earlier window, a skipped window, or a failed one)
    stops the watermark so no source dates disappear silently.
    """
    watermark = _watermark(db, channel_id, kind)
    rows = list(
        db.scalars(
            select(EditionWindowRecord)
            .where(
                EditionWindowRecord.channel_id == channel_id,
                EditionWindowRecord.kind == kind,
                EditionWindowRecord.status.in_(
                    ["committed", "no_content", "skipped", "collected", "awaiting_review", "built"]
                ),
            )
            .order_by(EditionWindowRecord.window_start.asc())
        )
    )
    if not rows:
        return watermark.last_cutoff_at if watermark else None
    committed_statuses = {"committed", "no_content"}
    pointer = as_utc(watermark.last_cutoff_at) if watermark else None
    for row in rows:
        start = as_utc(row.window_start)
        end = as_utc(row.window_end)
        if pointer is not None and end <= pointer:
            continue
        if pointer is not None and start > pointer:
            # Missing earlier window: stop at the known committed boundary.
            break
        if row.status in committed_statuses:
            pointer = end
            continue
        break
    if pointer is None:
        return None
    if watermark is None:
        db.add(EditionWatermark(channel_id=channel_id, kind=kind, last_cutoff_at=pointer))
        db.flush()
    elif pointer > as_utc(watermark.last_cutoff_at):
        watermark.last_cutoff_at = pointer
        watermark.updated_at = utcnow()
        db.flush()
    return pointer


def commit_window(
    db: Session,
    record: EditionWindowRecord,
    *,
    status: str = "committed",
    edition_id: int | None = None,
) -> None:
    record.status = status
    record.committed_at = utcnow()
    record.lock_owner = ""
    record.lock_expires_at = None
    record.updated_at = utcnow()
    if edition_id:
        record.edition_id = edition_id
    db.flush()
    recompute_watermark(db, record.channel_id, record.kind)


def commit_edition_window(db: Session, edition: Edition) -> EditionWindowRecord | None:
    """Called when an edition is released; advances the daily watermark."""
    if edition.kind not in {"daily", "weekly_roundup", "monthly_roundup", "manual", "special"}:
        return None
    record = db.scalar(
        select(EditionWindowRecord).where(
            EditionWindowRecord.channel_id == edition.channel_id,
            EditionWindowRecord.kind == edition.kind,
            EditionWindowRecord.window_end == edition.window_end,
        )
    )
    if record is None:
        return None
    # A special or manual edition must never advance the normal daily watermark
    # unless it was created as the scheduled daily window.
    if record.kind != edition.kind:
        return None
    commit_window(db, record, status="committed", edition_id=edition.id)
    return record


def record_for_edition(db: Session, edition: Edition) -> EditionWindowRecord | None:
    return db.scalar(
        select(EditionWindowRecord).where(
            EditionWindowRecord.channel_id == edition.channel_id,
            EditionWindowRecord.kind == edition.kind,
            EditionWindowRecord.window_end == edition.window_end,
        )
    )


def mark_window_built(db: Session, edition: Edition, *, status: str = "awaiting_review") -> None:
    record = record_for_edition(db, edition)
    if record is not None and record.status != "committed":
        record.status = status
        record.edition_id = edition.id
        record.updated_at = utcnow()
        db.flush()


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------


def _existing_message_ids(db: Session, channel_id: int, message_ids: list[int]) -> set[int]:
    if not message_ids:
        return set()
    return set(
        db.scalars(
            select(SourcePost.telegram_message_id).where(
                SourcePost.channel_id == channel_id,
                SourcePost.telegram_message_id.in_(message_ids),
            )
        )
    )


# ---------------------------------------------------------------------------
# durable retrieval staging
# ---------------------------------------------------------------------------
#
# Pagination is checkpointed after every page, but a message is only written to
# the database once the whole window has been fetched (so cross-page reply edges
# and albums stay coherent). Staging each page as JSON Lines means a crash on
# page 4 loses nothing: the next attempt reloads the staged pages and resumes
# from the checkpoint cursor. The file is removed after a successful import.


def staging_path(window: EditionWindow):
    name = hashlib.sha1(window.key.encode("utf-8")).hexdigest()[:24]
    folder = settings.data_dir / "window-staging"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{name}.jsonl"


def load_staged(path, window: EditionWindow) -> list[dict]:  # noqa: ANN001 - Path
    """Read previously staged pages, ignoring a torn final line."""
    if not path.exists():
        return []
    collected: dict[int, dict] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                page = json.loads(line)
            except json.JSONDecodeError:
                continue
            for raw in page.get("messages", []) or []:
                try:
                    message_id = int(raw.get("id"))
                except (TypeError, ValueError):
                    continue
                posted = parse_datetime(raw.get("date"))
                if posted is not None and window.contains(posted):
                    collected[message_id] = raw
    return list(collected.values())


def append_staged(path, messages: list[dict], page_no: int, lock: threading.Lock) -> None:  # noqa: ANN001
    if not messages:
        return
    line = json.dumps({"page": page_no, "messages": messages}, ensure_ascii=False, default=str)
    with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())


def collect_window(
    db: Session,
    channel: Channel,
    window: EditionWindow,
    source: WindowSource,
    *,
    rights: RightsGrant | None,
    actor_id: int | None = None,
    job_id: int | None = None,
    record: EditionWindowRecord | None = None,
) -> CollectionStats:
    if not channel.is_demo and rights is None:
        raise RightsGateError(
            "Collection blocked: this channel has no active rights declaration covering ingest."
        )
    stats = CollectionStats()
    record = record or get_or_create_window(db, window, job_id=job_id)
    checkpoint = checkpoint_for(db, window)
    if checkpoint is not None:
        # The gateway thread may have advanced the checkpoint through its own
        # connection; refresh so a retry resumes from the real cursor.
        db.refresh(checkpoint)
    resumable = checkpoint is not None and checkpoint.status in {"in_progress", "partial"}
    staged_file = staging_path(window)
    staged = load_staged(staged_file, window) if resumable else []
    if not resumable:
        staged_file.unlink(missing_ok=True)
    resume_offset = checkpoint.cursor_message_id if resumable and checkpoint else 0
    staged_lock = threading.Lock()
    progress = {"fetched": len(staged)}

    def _on_page(page: FetchedPage) -> None:  # runs on the gateway thread
        fresh: list[dict] = []
        for raw in page.messages:
            posted = parse_datetime(raw.get("date"))
            if posted is None or not window.contains(posted) or raw.get("id") is None:
                continue
            fresh.append(raw)
        # Stage before checkpointing: a crash between the two re-fetches one
        # page, which the id-keyed merge below de-duplicates.
        append_staged(staged_file, fresh, page.page, staged_lock)
        progress["fetched"] += len(fresh)
        try:
            from ..db import SessionLocal

            with SessionLocal() as side:
                save_checkpoint(
                    side,
                    window,
                    adapter=source.name,
                    page=page.page,
                    oldest_id=page.oldest_id,
                    fetched=progress["fetched"],
                )
                side.commit()
        except Exception:  # noqa: BLE001 - checkpointing must not break retrieval
            pass

    try:
        result = source.fetch_window(window, on_page=_on_page, resume_offset=resume_offset)
    except Exception as exc:
        save_checkpoint(
            db,
            window,
            adapter=source.name,
            page=checkpoint.pages if checkpoint else 0,
            oldest_id=resume_offset or None,
            fetched=progress["fetched"],
            status="partial",
            error=f"{type(exc).__name__}: {str(exc)[:400]}",
        )
        record.status = "failed"
        record.updated_at = utcnow()
        db.flush()
        raise

    # Merge staged pages from a previous attempt with this attempt's results.
    combined: dict[int, dict] = {}
    for raw in staged:
        combined[int(raw["id"])] = raw
    for raw in result.messages:
        try:
            combined[int(raw.get("id"))] = raw
        except (TypeError, ValueError):
            continue

    def _order(raw: dict) -> tuple[float, int]:
        posted = parse_datetime(raw.get("date"))
        return (posted.timestamp() if posted else 0.0, int(raw.get("id") or 0))

    # Defensive re-filter: pagination must never add a post outside [start, end).
    in_window: list[dict] = []
    out_of_window = 0
    for raw in sorted(combined.values(), key=_order):
        posted = parse_datetime(raw.get("date"))
        if posted is None or not window.contains(posted):
            out_of_window += 1
            continue
        in_window.append(raw)
    if out_of_window:
        stats.notes.append(f"{out_of_window} message(s) outside the window were discarded.")
    if len(staged):
        stats.notes.append(f"Resumed retrieval: {len(staged)} staged message(s) were reused.")

    stats.fetched = len(in_window)
    stats.pages = (checkpoint.pages if staged and checkpoint else 0) + result.pages
    stats.complete = result.complete
    stats.notes.extend(result.notes)
    message_ids = [int(raw.get("id")) for raw in in_window if raw.get("id") is not None]
    existing = _existing_message_ids(db, channel.id, message_ids)
    stats.duplicates = len(existing)
    stats.replies = len(
        [raw for raw in in_window if raw.get("reply_to_message_id") or raw.get("reply_to")]
    )
    stats.albums = len({raw.get("grouped_id") for raw in in_window if raw.get("grouped_id")})

    if in_window:
        payload = {"messages": in_window}
        imported = import_payload(
            db,
            channel,
            payload,
            imported_by=actor_id,
            source_kind=source.name,
            original_name=f"{source.name}-window-{window.end:%Y%m%dT%H%M%SZ}.json",
            rights_grant=rights,
            window_key=window.key,
        )
        stats.included = imported.accepted
        stats.excluded = imported.rejected
        stats.created = imported.created
        stats.updated = imported.updated
        stats.media = imported.media
    else:
        stats.notes.append("No source messages were published inside this window.")

    record.collected_at = utcnow()
    record.status = "collected"
    record.stats_json = json.dumps(stats.as_dict(), ensure_ascii=False)
    record.lock_owner = ""
    record.lock_expires_at = None
    record.updated_at = utcnow()
    save_checkpoint(
        db,
        window,
        adapter=source.name,
        page=stats.pages,
        oldest_id=None,
        fetched=stats.fetched,
        status="complete" if result.complete else "partial",
    )
    if result.complete:
        staged_file.unlink(missing_ok=True)
    audit(
        db,
        None,
        "window.collect",
        "channel",
        channel.id,
        {
            "window": window.key,
            "fetched": stats.fetched,
            "included": stats.included,
            "excluded": stats.excluded,
            "duplicates": stats.duplicates,
            "job": job_id,
        },
    )
    db.flush()
    return stats


def mark_no_content(db: Session, record: EditionWindowRecord, stats: CollectionStats) -> None:
    """A verified empty window is complete: commit it so no dates vanish."""
    record.status = "no_content"
    record.stats_json = json.dumps(stats.as_dict(), ensure_ascii=False)
    record.updated_at = utcnow()
    db.flush()
    commit_window(db, record, status="no_content")


def window_summary(db: Session, channel_id: int, limit: int = 12) -> list[dict]:
    rows = list(
        db.scalars(
            select(EditionWindowRecord)
            .where(EditionWindowRecord.channel_id == channel_id)
            .order_by(EditionWindowRecord.window_end.desc())
            .limit(limit)
        )
    )
    out = []
    for row in rows:
        stats = json.loads(row.stats_json or "{}")
        count = db.scalar(
            select(func.count())
            .select_from(SourcePost)
            .where(
                and_(
                    SourcePost.channel_id == channel_id,
                    SourcePost.posted_at >= row.window_start,
                    SourcePost.posted_at < row.window_end,
                )
            )
        )
        out.append({"record": row, "stats": stats, "stored_posts": int(count or 0)})
    return out


def preview_window(db: Session, schedule, now: datetime | None = None) -> dict:  # noqa: ANN001
    """Describe the next effective window without touching source data."""
    from ..scheduling.service import latest_due_cutoff  # local import avoids a cycle

    now = now or utcnow()
    watermark = _watermark(db, schedule.channel_id, schedule.kind)
    cutoff = latest_due_cutoff(schedule, now)
    if cutoff is None:
        window = window_for_schedule(schedule, now)
        cutoff = window.end
    else:
        window = window_for_schedule(schedule, cutoff)
    count = db.scalar(
        select(func.count())
        .select_from(SourcePost)
        .where(
            SourcePost.channel_id == schedule.channel_id,
            SourcePost.posted_at >= window.start,
            SourcePost.posted_at < window.end,
            SourcePost.inclusion_status == "included",
        )
    )
    return {
        "window": window.as_dict(),
        "watermark": as_utc(watermark.last_cutoff_at).isoformat() if watermark else None,
        "stored_included_posts": int(count or 0),
        "first_of_series": watermark is None,
        "context_lookback_days": settings.initial_context_lookback_days,
    }
