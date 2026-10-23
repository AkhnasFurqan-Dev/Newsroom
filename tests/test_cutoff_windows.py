"""Executable checks for the cutoff-to-cutoff window contract.

Each test maps to one of the required acceptance cases in
``POST_M14_LIVE_COMPLETION.md`` section 4. All timestamps are UTC in the
database; local clock times are converted through an IANA timezone.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select

from channel_press.collection.service import (
    commit_edition_window,
    commit_window,
    get_or_create_window,
    preview_window,
    recompute_watermark,
)
from channel_press.collection.window import (
    EditionWindow,
    cutoff_for_day,
    due_cutoffs,
    local_cutoff_utc,
    window_for_cutoff,
    window_for_schedule,
)
from channel_press.ingestion.service import import_payload
from channel_press.models import (
    Edition,
    EditionSchedule,
    EditionWatermark,
    EditionWindowRecord,
    PostVersion,
    SourcePost,
)
from channel_press.scheduling.service import (
    enqueue_due_jobs,
    enqueue_manual_job,
    latest_due_cutoff,
)


def _schedule(db, channel, **overrides) -> EditionSchedule:
    schedule = db.scalar(
        select(EditionSchedule).where(
            EditionSchedule.channel_id == channel.id, EditionSchedule.kind == "daily"
        )
    )
    schedule.enabled = True
    for key, value in overrides.items():
        setattr(schedule, key, value)
    db.commit()
    return schedule


def _window(
    kind: str = "daily",
    start: datetime = None,  # type: ignore[assignment]
    end: datetime = None,  # type: ignore[assignment]
    channel_id: int = 1,
) -> EditionWindow:
    start = start or datetime(2026, 10, 19, 3, 0, tzinfo=UTC)
    end = end or datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
    return EditionWindow(channel_id=channel_id, kind=kind, start=start, end=end)


# ---------------------------------------------------------------------------
# case 1: half-open interval, start inclusive, cutoff excluded
# ---------------------------------------------------------------------------


def test_start_inclusive_and_cutoff_exclusive_at_one_microsecond():
    start = local_cutoff_utc(date(2026, 10, 19), time(8, 0), "Asia/Karachi")
    end = local_cutoff_utc(date(2026, 10, 20), time(8, 0), "Asia/Karachi")
    window = _window(start=start, end=end)

    assert window.contains(start)
    assert window.contains(end - timedelta(microseconds=1))
    assert window.contains(start + timedelta(seconds=1))
    assert not window.contains(end)
    assert not window.contains(end + timedelta(microseconds=1))
    assert window.as_dict()["semantics"] == "start inclusive, end exclusive"


def test_a_post_exactly_at_cutoff_belongs_to_the_next_issue():
    first = _window(
        start=datetime(2026, 10, 19, 3, 0, tzinfo=UTC),
        end=datetime(2026, 10, 20, 3, 0, tzinfo=UTC),
    )
    second = _window(
        start=first.end,
        end=datetime(2026, 10, 21, 3, 0, tzinfo=UTC),
    )
    exact = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)

    assert not first.contains(exact)
    assert second.contains(exact)


# ---------------------------------------------------------------------------
# case 3: a late worker keeps the original cutoff
# ---------------------------------------------------------------------------


def test_late_worker_uses_the_scheduled_cutoff_not_the_wake_time(db, demo_channel):
    schedule = _schedule(db, demo_channel, cutoff_time="08:00", release_time="11:00")
    # Tuesday 11:00 Asia/Karachi = 06:00 UTC. The cutoff was 08:00 local (03:00 UTC).
    late_start = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
    cutoff = latest_due_cutoff(schedule, late_start)
    assert cutoff == datetime(2026, 10, 20, 3, 0, tzinfo=UTC)

    window = window_for_schedule(schedule, cutoff)
    assert window.start == datetime(2026, 10, 19, 3, 0, tzinfo=UTC)
    assert window.end == cutoff
    # A post published after the wake-up time is future news, not this issue.
    assert not window.contains(datetime(2026, 10, 20, 6, 0, tzinfo=UTC))


# ---------------------------------------------------------------------------
# case 5: concurrent claims and expired leases
# ---------------------------------------------------------------------------


def test_only_one_worker_can_claim_a_window(db, demo_channel):
    from channel_press.collection.service import claim_window

    window = _window(channel_id=demo_channel.id)
    record = get_or_create_window(db, window)
    db.commit()

    assert claim_window(db, record, owner="worker-a")
    db.commit()
    assert record.status == "collecting"
    assert not claim_window(db, record, owner="worker-b")
    db.rollback()

    # An expired lease can be reclaimed deliberately; a live one cannot.
    record = db.get(EditionWindowRecord, record.id)
    record.lock_expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()
    assert claim_window(db, record, owner="worker-b")
    db.commit()
    assert record.lock_owner == "worker-b"


# ---------------------------------------------------------------------------
# case 6: repeated edits leave one latest eligible item with provenance
# ---------------------------------------------------------------------------


def test_two_prebuild_edits_keep_one_post_and_a_version_history(db, demo_channel):
    base = {
        "id": 555,
        "date": "2026-10-19T04:00:00Z",
        "text": "First version of the report.",
    }
    import_payload(
        db, demo_channel, {"messages": [base]}, imported_by=None, source_kind="fixture"
    )
    db.commit()

    edited = {
        "id": 555,
        "date": "2026-10-19T04:00:00Z",
        "edited_at": "2026-10-19T05:00:00Z",
        "text": "Second version of the report, with the corrected figure.",
    }
    import_payload(
        db, demo_channel, {"messages": [edited]}, imported_by=None, source_kind="fixture"
    )
    db.commit()

    posts = list(
        db.scalars(
            select(SourcePost).where(
                SourcePost.channel_id == demo_channel.id,
                SourcePost.telegram_message_id == 555,
            )
        )
    )
    assert len(posts) == 1
    post = posts[0]
    assert "corrected figure" in post.text
    versions = list(
        db.scalars(select(PostVersion).where(PostVersion.post_id == post.id).order_by(PostVersion.version))
    )
    assert [row.version for row in versions] == [1, 2]
    assert versions[0].content_hash != versions[1].content_hash


# ---------------------------------------------------------------------------
# case 7: replies land in the window of their own timestamp
# ---------------------------------------------------------------------------


def test_reply_after_cutoff_belongs_to_the_next_window():
    first = _window(
        start=datetime(2026, 10, 19, 3, 0, tzinfo=UTC),
        end=datetime(2026, 10, 20, 3, 0, tzinfo=UTC),
    )
    second = _window(
        start=first.end,
        end=datetime(2026, 10, 21, 3, 0, tzinfo=UTC),
    )
    original = datetime(2026, 10, 20, 2, 55, tzinfo=UTC)
    reply = datetime(2026, 10, 20, 3, 1, tzinfo=UTC)

    assert first.contains(original)
    assert not second.contains(original)
    assert second.contains(reply)
    assert not first.contains(reply)


# ---------------------------------------------------------------------------
# case 8: special/roundup editions never advance the daily watermark
# ---------------------------------------------------------------------------


def test_special_issue_does_not_advance_the_daily_watermark(db, demo_channel):
    daily = _window(channel_id=demo_channel.id)
    daily_record = get_or_create_window(db, daily)
    commit_window(db, daily_record, status="committed")
    db.commit()

    watermark = db.scalar(
        select(EditionWatermark).where(
            EditionWatermark.channel_id == demo_channel.id,
            EditionWatermark.kind == "daily",
        )
    )
    assert watermark is not None and watermark.last_cutoff_at == daily.end

    special_window = _window(
        kind="special",
        start=datetime(2026, 10, 19, 12, 0, tzinfo=UTC),
        end=datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
        channel_id=demo_channel.id,
    )
    special_record = get_or_create_window(db, special_window)
    special = Edition(
        channel_id=demo_channel.id,
        kind="special",
        title="Special",
        status="awaiting_review",
        window_start=special_window.start,
        window_end=special_window.end,
        cutoff_at=special_window.end,
        target_release_at=special_window.end,
        timezone="Asia/Karachi",
    )
    db.add(special)
    db.flush()
    commit_edition_window(db, special)
    db.commit()

    daily_watermark = db.scalar(
        select(EditionWatermark).where(
            EditionWatermark.channel_id == demo_channel.id,
            EditionWatermark.kind == "daily",
        )
    )
    assert daily_watermark.last_cutoff_at == daily.end
    assert special_record.status == "committed"
    # The special edition's own series has its own watermark, isolated by kind.
    special_watermark = db.scalar(
        select(EditionWatermark).where(
            EditionWatermark.channel_id == demo_channel.id,
            EditionWatermark.kind == "special",
        )
    )
    assert special_watermark is not None
    assert special_watermark.last_cutoff_at == special_window.end


# ---------------------------------------------------------------------------
# case 9: missed cutoffs are caught up in order or explicitly skipped
# ---------------------------------------------------------------------------


def test_missed_cutoffs_queue_in_order_and_record_skipped_days(db, demo_channel):
    schedule = _schedule(db, demo_channel)
    now = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)  # Thursday 11:00 PKT
    watermark_cutoff = cutoff_for_day(schedule, date(2026, 10, 18))
    db.add(
        EditionWatermark(
            channel_id=demo_channel.id,
            kind="daily",
            last_cutoff_at=watermark_cutoff,
        )
    )
    db.commit()

    window_cutoffs, skipped = due_cutoffs(
        schedule, watermark=watermark_cutoff, now=now, max_catchup=2, horizon_days=10
    )
    assert skipped, "older missed windows must be reported, not hidden"
    assert len(window_cutoffs) == 2
    assert window_cutoffs == sorted(window_cutoffs)
    assert all(cutoff > watermark_cutoff for cutoff in window_cutoffs)

    created = enqueue_due_jobs(db, now=now, max_catchup=2)
    db.commit()
    assert len(created) == 2
    queued = sorted(json.loads(job.payload_json)["cutoff"] for job in created)
    assert queued == [cutoff.isoformat() for cutoff in window_cutoffs]

    skipped_rows = list(
        db.scalars(
            select(EditionWindowRecord).where(
                EditionWindowRecord.channel_id == demo_channel.id,
                EditionWindowRecord.status == "skipped",
            )
        )
    )
    assert len(skipped_rows) == len(skipped)
    assert all(row.skip_reason for row in skipped_rows)
    assert all(row.committed_at is None for row in skipped_rows)


# ---------------------------------------------------------------------------
# case 10: documented daylight-saving policy
# ---------------------------------------------------------------------------


def test_spring_forward_gap_resolves_forward():
    # 02:30 does not exist on 2026-03-08 in New York. The documented policy
    # resolves it with the pre-transition offset: 07:30 UTC (03:30 EDT).
    resolved = local_cutoff_utc(date(2026, 3, 8), time(2, 30), "America/New_York")
    assert resolved == datetime(2026, 3, 8, 7, 30, tzinfo=UTC)


def test_fall_back_ambiguous_time_uses_first_occurrence():
    # 01:30 happens twice on 2026-11-01. fold=0 is the earlier UTC instant.
    resolved = local_cutoff_utc(date(2026, 11, 1), time(1, 30), "America/New_York")
    assert resolved == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


def test_window_over_a_dst_transition_covers_real_elapsed_time():
    window = window_for_cutoff(
        channel_id=1,
        kind="daily",
        timezone="America/New_York",
        cutoff_clock="08:00",
        weekdays="0,1,2,3,4,5,6",
        cutoff=local_cutoff_utc(date(2026, 11, 1), time(8, 0), "America/New_York"),
    )
    # 08:00 EDT on 31 October -> 08:00 EST on 1 November is 25 real hours.
    assert window.end - window.start == timedelta(hours=25)


# ---------------------------------------------------------------------------
# case 11: context backfill never changes the active daily window
# ---------------------------------------------------------------------------


def test_context_backfill_does_not_move_the_daily_watermark_or_window(db, demo_channel):
    schedule = _schedule(db, demo_channel)
    context = _window(
        kind="context",
        start=datetime(2026, 9, 20, 3, 0, tzinfo=UTC),
        end=datetime(2026, 10, 20, 3, 0, tzinfo=UTC),
        channel_id=demo_channel.id,
    )
    record = get_or_create_window(db, context, status="context")
    record.status = "context"
    db.commit()

    assert recompute_watermark(db, demo_channel.id, "daily") is None
    db.rollback()

    now = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)
    preview = preview_window(db, schedule, now=now)
    expected = window_for_schedule(schedule, latest_due_cutoff(schedule, now))
    assert preview["window"]["key"] == expected.key
    assert preview["first_of_series"] is True
    assert preview["context_lookback_days"] >= 30
    # The preview is a single day, not the 30-day backfill span.
    assert preview["window"]["end_utc"] != context.end.isoformat()


# ---------------------------------------------------------------------------
# enqueue idempotency and manual windows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["manual", "special"])
def test_manual_window_records_are_created_for_traceability(db, demo_channel, kind):
    now = datetime(2026, 10, 22, tzinfo=UTC)
    job = enqueue_manual_job(
        db,
        demo_channel,
        window_start=now - timedelta(days=1),
        window_end=now,
        kind=kind,
    )
    db.commit()
    payload = json.loads(job.payload_json)
    assert payload["window_end"].startswith("2026-10-22T00:00:00")
    assert payload["collect"] is False, "the fixture channel never collects over the network"
    record = get_or_create_window(
        db,
        EditionWindow(
            channel_id=demo_channel.id,
            kind=kind,
            start=now - timedelta(days=1),
            end=now,
        ),
        job_id=job.id,
    )
    db.commit()
    assert record.job_id == job.id
