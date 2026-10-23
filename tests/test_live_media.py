"""Lazy source-media download for live channels.

Metadata is stored at import time. The actual file is fetched only when a story
reaches layout, once, within the configured video budget.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image
from sqlalchemy import select

from channel_press.config import settings
from channel_press.media.service import ensure_media_files
from channel_press.models import Channel, PostFragment, PostMedia, SourcePost, StoryCluster
from channel_press.telegram.service import telegram_service


def _live_channel(db) -> Channel:
    channel = Channel(
        slug="live_desk",
        title="Live Desk",
        handle="@live_desk",
        is_public=False,
        is_demo=False,
    )
    db.add(channel)
    db.flush()
    return channel


def _post_with_media(db, channel, *, kind="photo", file_id="file-1", declared_bytes=2048, grouped=False):
    raw = {
        "id": 77,
        "date": datetime(2026, 10, 19, 5, 0, tzinfo=UTC).isoformat(),
        "text": "A verified report with source photographs of the damaged bridge.",
        "media": [{"kind": kind, "telegram_file_id": file_id, "bytes": declared_bytes}],
    }
    post = SourcePost(
        channel_id=channel.id,
        telegram_message_id=77,
        posted_at=datetime(2026, 10, 19, 5, 0, tzinfo=UTC),
        text=raw["text"],
        inclusion_status="included",
        raw_json=json.dumps(raw),
        content_hash="a" * 64,
    )
    db.add(post)
    db.flush()
    media = PostMedia(
        post_id=post.id,
        kind=kind,
        telegram_file_id=file_id,
        original_path="",
        width=0,
        height=0,
        caption="Source photograph.",
        credit="Live Desk",
    )
    db.add(media)
    db.flush()
    story = StoryCluster(
        channel_id=channel.id,
        canonical_title="Bridge damage report",
        fingerprint="lazy-media-story",
        section="national",
        genre="news",
        risk_level="low",
    )
    db.add(story)
    db.flush()
    db.add(
        PostFragment(
            post_id=post.id,
            story_id=story.id,
            text=raw["text"],
            start_offset=0,
            end_offset=len(raw["text"]),
            language="en",
            topics_json="{}",
            sequence=0,
            editorial_unit_key=f"p:{post.id}",
        )
    )
    db.commit()
    return post, media, story


def test_remote_photo_is_downloaded_once_when_the_story_reaches_layout(db, monkeypatch):
    channel = _live_channel(db)
    post, media, story = _post_with_media(db, channel)
    calls: list[tuple[str, int, Path]] = []

    def fake_download(handle, message_id, target):
        calls.append((handle, message_id, Path(target)))
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1200, 800), (80, 90, 100)).save(target, format="JPEG")
        return str(target)

    monkeypatch.setattr(telegram_service, "credentials_configured", lambda: True)
    monkeypatch.setattr(telegram_service, "download_media", fake_download)

    assert ensure_media_files(db, story) == 1
    db.commit()
    assert calls == [("@live_desk", 77, Path(media.original_path))]
    assert Path(media.original_path).exists()
    assert media.sha256 and len(media.sha256) == 64
    assert (media.width, media.height) == (1200, 800)

    # A second layout pass must not download the same asset again.
    assert ensure_media_files(db, story) == 0
    assert len(calls) == 1


def test_video_over_the_configured_budget_is_not_downloaded(db, monkeypatch):
    channel = _live_channel(db)
    oversized = settings.telegram_max_video_mb + 50
    post, media, story = _post_with_media(
        db,
        channel,
        kind="video",
        file_id="video-1",
        declared_bytes=oversized * 1024 * 1024,
    )
    calls = []
    monkeypatch.setattr(telegram_service, "credentials_configured", lambda: True)
    monkeypatch.setattr(
        telegram_service,
        "download_media",
        lambda handle, message_id, target: calls.append(target),
    )

    assert ensure_media_files(db, story) == 0
    assert calls == []
    assert media.original_path == ""


def test_demo_channel_never_downloads_remote_media(db, demo_channel, monkeypatch):
    post, media, story = _post_with_media(db, demo_channel)
    calls = []
    monkeypatch.setattr(telegram_service, "credentials_configured", lambda: True)
    monkeypatch.setattr(
        telegram_service,
        "download_media",
        lambda handle, message_id, target: calls.append(target),
    )
    assert ensure_media_files(db, story) == 0
    assert calls == []


def test_missing_credentials_leave_the_metadata_record_intact(db, monkeypatch):
    channel = _live_channel(db)
    post, media, story = _post_with_media(db, channel)
    monkeypatch.setattr(telegram_service, "credentials_configured", lambda: False)
    assert ensure_media_files(db, story) == 0
    refreshed = db.scalar(select(PostMedia).where(PostMedia.id == media.id))
    assert refreshed is not None
    assert refreshed.telegram_file_id == "file-1"
    assert refreshed.original_path == ""
