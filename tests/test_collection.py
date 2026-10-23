"""Collection, retry, idempotency and watermark tests (section 4 cases 2, 4, 8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from channel_press.collection.service import (
    FixtureWindowSource,
    FetchedPage,
    SourceResult,
    claim_window,
    collect_window,
    commit_window,
    get_or_create_window,
    mark_no_content,
    recompute_watermark,
    staging_path,
)
from channel_press.collection.window import EditionWindow
from channel_press.db import SessionLocal
from channel_press.models import (
    EditionWatermark,
    PostFragment,
    RetrievalCheckpoint,
    SourceImport,
    SourcePost,
)
from channel_press.telegram.transport import TelegramTransportError
from channel_press.timeutil import parse_datetime


WINDOW = EditionWindow(
    channel_id=1,
    kind="daily",
    start=datetime(2026, 10, 19, 3, 0, tzinfo=UTC),
    end=datetime(2026, 10, 20, 3, 0, tzinfo=UTC),
)


def _messages(count: int = 150) -> list[dict]:
    base = datetime(2026, 10, 19, 4, 0, tzinfo=UTC)
    rows = []
    for index in range(1, count + 1):
        raw = {
            "id": index,
            "date": (base + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
            "text": (
                f"Report number {index} from the district desk with verified details "
                "about the local situation and the response of the authorities."
            ),
        }
        if index <= 20:
            raw["grouped_id"] = 9000 + (index - 1) // 2
        rows.append(raw)
    return rows


class PagedSource:
    """Deterministic newest-first paginator with an optional injected failure."""

    name = "fake-telegram"

    def __init__(self, messages: list[dict], page_size: int = 10, fail_on_page: int | None = None):
        self.messages = messages
        self.page_size = page_size
        self.fail_on_page = fail_on_page
        self.calls = 0

    def fetch_window(self, window, *, on_page=None, resume_offset: int = 0):
        self.calls += 1
        selected = []
        for raw in self.messages:
            posted = parse_datetime(raw.get("date"))
            if posted is None or not window.contains(posted):
                continue
            if resume_offset and int(raw["id"]) >= int(resume_offset):
                continue
            selected.append(raw)
        selected.sort(key=lambda item: int(item["id"]), reverse=True)
        pages = [selected[i : i + self.page_size] for i in range(0, len(selected), self.page_size)]
        for number, page in enumerate(pages, start=1):
            if self.fail_on_page and number == self.fail_on_page:
                raise TelegramTransportError(f"simulated network failure on page {number}")
            if on_page is not None:
                on_page(FetchedPage(page=number, messages=page, oldest_id=int(page[-1]["id"])))
        return SourceResult(channel={"id": 1}, messages=selected, pages=len(pages), complete=True)


def _window_for(channel) -> EditionWindow:
    return EditionWindow(
        channel_id=channel.id,
        kind="daily",
        start=WINDOW.start,
        end=WINDOW.end,
    )


# ---------------------------------------------------------------------------
# zero content
# ---------------------------------------------------------------------------


def test_a_verified_empty_window_is_committed_so_no_date_disappears(db, demo_channel):
    window = _window_for(demo_channel)
    record = get_or_create_window(db, window, status="pending")
    db.commit()

    stats = collect_window(
        db,
        demo_channel,
        window,
        FixtureWindowSource([]),
        rights=None,
    )
    db.commit()
    assert stats.fetched == 0
    assert stats.included == 0
    assert record.status == "collected"

    mark_no_content(db, record, stats)
    db.commit()
    assert record.status == "no_content"
    assert record.committed_at is not None
    watermark = db.scalar(
        select(EditionWatermark).where(
            EditionWatermark.channel_id == demo_channel.id,
            EditionWatermark.kind == "daily",
        )
    )
    assert watermark is not None and watermark.last_cutoff_at == window.end


def test_forwards_and_promotions_are_excluded_but_never_published_as_news(db, demo_channel):
    window = _window_for(demo_channel)
    get_or_create_window(db, window)
    db.commit()
    messages = [
        {
            "id": 1,
            "date": "2026-10-19T05:00:00Z",
            "text": "Forwarded claim from another desk that must be attributed elsewhere.",
            "forward_from": "Another Telegram channel",
        },
        {
            "id": 2,
            "date": "2026-10-19T05:05:00Z",
            "text": "Join our channel for more updates: https://t.me/example",
        },
        {
            "id": 3,
            "date": "2026-10-19T05:10:00Z",
            "text": (
                "The district administration said the road would reopen on Tuesday "
                "after engineers completed a safety inspection of the bridge."
            ),
        },
    ]
    stats = collect_window(db, demo_channel, window, FixtureWindowSource(messages), rights=None)
    db.commit()

    assert stats.fetched == 3
    assert stats.included == 1
    assert stats.excluded == 2
    statuses = {
        post.telegram_message_id: post.inclusion_status
        for post in db.scalars(
            select(SourcePost).where(SourcePost.channel_id == demo_channel.id)
        )
    }
    assert statuses == {1: "excluded", 2: "excluded", 3: "included"}
    reasons = {
        post.telegram_message_id: post.exclusion_reason
        for post in db.scalars(
            select(SourcePost).where(SourcePost.channel_id == demo_channel.id)
        )
    }
    assert reasons[1] == "telegram_forward"
    assert reasons[2] == "promotion_only"


# ---------------------------------------------------------------------------
# 150 posts, pagination, albums
# ---------------------------------------------------------------------------


def test_150_posts_paginate_completely_and_albums_share_one_editorial_unit(db, demo_channel):
    window = _window_for(demo_channel)
    get_or_create_window(db, window)
    db.commit()

    source = PagedSource(_messages(150), page_size=10)
    stats = collect_window(db, demo_channel, window, source, rights=None)
    db.commit()

    assert stats.fetched == 150
    assert stats.pages == 15
    assert stats.included == 150
    posts = list(
        db.scalars(select(SourcePost).where(SourcePost.channel_id == demo_channel.id))
    )
    assert len(posts) == 150
    assert len({post.telegram_message_id for post in posts}) == 150

    fragments = list(
        db.scalars(
            select(PostFragment).where(
                PostFragment.post_id.in_([post.id for post in posts])
            )
        )
    )
    units = {fragment.editorial_unit_key for fragment in fragments}
    # Ten two-message albums collapse to ten editorial units, so 140 not 150.
    assert len(units) == 140

    checkpoint = db.scalar(
        select(RetrievalCheckpoint).where(RetrievalCheckpoint.window_key == window.key)
    )
    assert checkpoint is not None
    assert checkpoint.status == "complete"
    assert checkpoint.pages == 15
    assert checkpoint.fetched_count == 150


# ---------------------------------------------------------------------------
# failure at page 4, restart, no loss and no duplicates
# ---------------------------------------------------------------------------


def test_failure_on_page_four_resumes_without_losing_or_duplicating_posts(db, demo_channel):
    window = _window_for(demo_channel)
    record = get_or_create_window(db, window)
    db.commit()
    staged = staging_path(window)
    staged.unlink(missing_ok=True)

    failing = PagedSource(_messages(150), page_size=10, fail_on_page=4)
    with pytest.raises(TelegramTransportError):
        collect_window(db, demo_channel, window, failing, rights=None)
    db.commit()

    assert record.status == "failed"
    checkpoint = db.scalar(
        select(RetrievalCheckpoint).where(RetrievalCheckpoint.window_key == window.key)
    )
    assert checkpoint is not None
    assert checkpoint.status == "partial"
    assert checkpoint.cursor_message_id == 121  # oldest id staged from page 3
    assert checkpoint.pages == 3
    assert staged.exists(), "staged pages must survive the failed attempt"
    assert "simulated" in checkpoint.last_error

    # Nothing is in the editorial database yet: the window is not half-imported.
    assert db.scalar(select(SourcePost).where(SourcePost.channel_id == demo_channel.id)) is None

    resumed = PagedSource(_messages(150), page_size=10)
    stats = collect_window(db, demo_channel, window, resumed, rights=None)
    db.commit()

    assert stats.fetched == 150
    assert stats.pages == 15
    assert stats.complete is True
    assert any("Resumed retrieval" in note for note in stats.notes)
    posts = list(
        db.scalars(select(SourcePost).where(SourcePost.channel_id == demo_channel.id))
    )
    assert len(posts) == 150
    ids = [post.telegram_message_id for post in posts]
    assert len(ids) == len(set(ids))
    assert sorted(ids) == list(range(1, 151))
    assert stats.duplicates == 0
    assert not staged.exists()
    checkpoint = db.scalar(
        select(RetrievalCheckpoint).where(RetrievalCheckpoint.window_key == window.key)
    )
    assert checkpoint.status == "complete"

    # The retry reuses the same durable window key and import provenance.
    imports = list(
        db.scalars(select(SourceImport).where(SourceImport.channel_id == demo_channel.id))
    )
    assert imports and all(row.window_key == window.key for row in imports)


# ---------------------------------------------------------------------------
# rerun / idempotency
# ---------------------------------------------------------------------------


def test_recollecting_the_same_window_is_idempotent(db, demo_channel):
    window = _window_for(demo_channel)
    get_or_create_window(db, window)
    db.commit()
    messages = _messages(30)

    first = collect_window(db, demo_channel, window, PagedSource(messages), rights=None)
    db.commit()
    assert first.created == 30

    second = collect_window(db, demo_channel, window, PagedSource(messages), rights=None)
    db.commit()
    assert second.duplicates == 30
    assert second.created == 0
    assert db.scalar(select(SourcePost).where(SourcePost.channel_id == demo_channel.id)) is not None
    total = len(list(db.scalars(select(SourcePost).where(SourcePost.channel_id == demo_channel.id))))
    assert total == 30


# ---------------------------------------------------------------------------
# concurrent claims across sessions
# ---------------------------------------------------------------------------


def test_two_sessions_cannot_collect_the_same_window(db, demo_channel):
    window = _window_for(demo_channel)
    record = get_or_create_window(db, window)
    db.commit()

    with SessionLocal() as first:
        claimed = claim_window(first, first.get(type(record), record.id), owner="worker-a")
        first.commit()
        assert claimed
    with SessionLocal() as second:
        claimed = claim_window(second, second.get(type(record), record.id), owner="worker-b")
        second.commit()
        assert not claimed

    fresh = db.get(type(record), record.id)
    db.refresh(fresh)
    assert fresh.lock_owner == "worker-a"


# ---------------------------------------------------------------------------
# watermark discipline
# ---------------------------------------------------------------------------


def test_watermark_moves_only_when_the_window_is_committed(db, demo_channel):
    window = _window_for(demo_channel)
    record = get_or_create_window(db, window)
    db.commit()

    stats = collect_window(
        db, demo_channel, window, FixtureWindowSource(_messages(5)), rights=None
    )
    assert stats.fetched == 5
    db.commit()
    assert record.status == "collected"
    assert recompute_watermark(db, demo_channel.id, "daily") is None

    commit_window(db, record, status="committed")
    db.commit()
    assert recompute_watermark(db, demo_channel.id, "daily") == window.end

    watermark = db.scalar(
        select(EditionWatermark).where(
            EditionWatermark.channel_id == demo_channel.id,
            EditionWatermark.kind == "daily",
        )
    )
    assert watermark.last_cutoff_at == window.end


def test_an_uncommitted_gap_stops_the_watermark(db, demo_channel):
    first = EditionWindow(
        channel_id=demo_channel.id,
        kind="daily",
        start=datetime(2026, 10, 17, 3, 0, tzinfo=UTC),
        end=datetime(2026, 10, 18, 3, 0, tzinfo=UTC),
    )
    gap = EditionWindow(
        channel_id=demo_channel.id,
        kind="daily",
        start=first.end,
        end=datetime(2026, 10, 19, 3, 0, tzinfo=UTC),
    )
    third = EditionWindow(
        channel_id=demo_channel.id,
        kind="daily",
        start=gap.end,
        end=datetime(2026, 10, 20, 3, 0, tzinfo=UTC),
    )
    for window, status in ((first, "committed"), (gap, "collected"), (third, "committed")):
        record = get_or_create_window(db, window)
        if status == "committed":
            commit_window(db, record, status="committed")
        else:
            record.status = status
        db.commit()

    # Contiguous commitment stops at the gap; the later commit is not allowed
    # to leap over an unapproved day.
    assert recompute_watermark(db, demo_channel.id, "daily") == first.end
