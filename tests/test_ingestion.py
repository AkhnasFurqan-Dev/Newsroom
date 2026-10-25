from __future__ import annotations

from datetime import UTC, datetime

import pytest

from channel_press.config import settings
from channel_press.ingestion.adapters import (
    ImportFormatError,
    NormalizedMessage,
    extract_links,
    load_json,
    normalize_messages,
)
from channel_press.ingestion.normalize import detect_language, exclusion_reason, promotion_only, split_segments
from channel_press.ingestion.service import (
    apply_deletion,
    source_window,
)
from channel_press.models import (
    Channel,
    PostMedia,
    PostVersion,
    ReplyEdge,
    SourcePost,
)
from tests.conftest import FIXTURES, import_fixture


def test_extract_links_strips_sentence_punctuation_but_keeps_balanced_brackets():
    text = (
        "The portal is at https://example.gov.pk/flood-relief. "
        "Mirror (see https://example.org/a_(b)) and https://example.net/x, "
        "then https://example.com/path?q=1!"
    )
    assert extract_links(text) == [
        "https://example.gov.pk/flood-relief",
        "https://example.org/a_(b)",
        "https://example.net/x",
        "https://example.com/path?q=1",
    ]
    assert extract_links("No links here.") == []
    assert extract_links("https://example.com/same https://example.com/same") == ["https://example.com/same"]


def test_normalize_owner_format_and_telegram_desktop_format():
    owner_payload = {
        "messages": [
            {
                "id": 1,
                "date": "2026-10-20T06:00:00Z",
                "text": [{"type": "bold", "text": "Hello "}, {"type": "link", "text": "report", "href": "https://example.com"}],
                "grouped_id": "42",
                "reply_to_message_id": "9",
                "translation_en": "Hello report",
                "translator": "source-provided",
            }
        ]
    }
    messages = normalize_messages(owner_payload)
    assert len(messages) == 1
    assert messages[0].text == "Hello report"
    assert messages[0].grouped_id == 42
    assert messages[0].reply_to_message_id == 9
    assert messages[0].translation_en == "Hello report"

    desktop = {
        "chats": [
            {"type": "public_channel", "messages": [{"id": 5, "date": "2026-10-20T07:00:00Z", "text": "Desktop export"}]},
            {"type": "personal_chat", "messages": [{"id": 6, "date": "2026-10-20T07:00:00Z", "text": "Ignored"}]},
        ]
    }
    messages = normalize_messages(desktop)
    assert [message.message_id for message in messages] == [5]


def test_normalize_rejects_invalid_shapes():
    with pytest.raises(ImportFormatError):
        normalize_messages({"messages": "not-a-list"})
    assert normalize_messages({"messages": []}) == []
    with pytest.raises(ImportFormatError):
        normalize_messages({"messages": [{"id": 1, "date": "not-a-date", "text": "x"}]})


def test_exclusion_policy_covers_forwards_promotions_empty_and_rights():
    def message(**kwargs):
        base = {
            "message_id": 1,
            "posted_at": datetime(2026, 10, 20, tzinfo=UTC),
            "text": "Security forces reopened the highway after clearance work.",
        }
        base.update(kwargs)
        return NormalizedMessage(**base)

    assert exclusion_reason(message(), rights_ok=True) is None
    assert exclusion_reason(message(forward_origin="Other channel"), rights_ok=True) == "telegram_forward"
    assert exclusion_reason(message(text="Join our new channel for updates"), rights_ok=True) == "promotion_only"
    assert exclusion_reason(message(text=""), rights_ok=True) == "empty"
    assert exclusion_reason(message(deleted=True), rights_ok=True) == "deleted"
    assert exclusion_reason(message(), rights_ok=False) == "rights_unconfirmed"
    assert promotion_only(message(text="Follow us at https://t.me/example")) is True
    assert promotion_only(message(text="The district administration issued a statement about the road.")) is False


def test_language_and_segment_helpers_preserve_source_text():
    assert detect_language("سرحدی تجارتی راستے کی بندش") == "ur"
    assert detect_language("The road reopened on Tuesday.") == "en"
    message = NormalizedMessage(
        message_id=3,
        posted_at=datetime(2026, 10, 20, tzinfo=UTC),
        text="First incident happened here.\n\nSecond incident happened there.",
        segments=[
            {"text": "First incident happened here.", "topic": "conflict", "title": "First"},
            {"text": "Second incident happened there.", "topic": "regional", "title": "Second"},
        ],
    )
    fragments = split_segments(message)
    assert len(fragments) == 2
    assert [fragment["title"] for fragment in fragments] == ["First", "Second"]
    assert fragments[0]["sequence"] == 0


def test_sample_import_is_idempotent_and_copies_media(db, demo_channel, owner):
    stats = import_fixture(db, demo_channel, owner, "telegram_export_sample.json")
    assert stats.raw_count == 26
    assert stats.created == 26
    assert stats.accepted == 22
    assert stats.rejected == 4
    assert stats.media == 6

    posts = list(db.query(SourcePost).order_by(SourcePost.telegram_message_id))
    assert len(posts) == 26
    excluded = {post.telegram_message_id: post.exclusion_reason for post in posts if post.inclusion_status == "excluded"}
    assert excluded == {
        901: "promotion_only",
        902: "telegram_forward",
        903: "empty",
        905: "deleted",
    }
    assert sum(1 for post in posts if post.deleted_at is not None) == 1
    assert all(post.source_url == "" for post in posts)  # private demo channel

    media = list(db.query(PostMedia))
    assert len(media) == 6
    for item in media:
        assert item.original_path
        assert item.sha256
        assert str(settings.media_dir) in item.original_path
    assert any(item.kind == "video" for item in media)

    # Re-importing identical content must not duplicate posts, versions or media.
    again = import_fixture(db, demo_channel, owner, "telegram_export_sample.json")
    assert again.created == 0
    assert again.updated == 0
    assert again.media == 0
    assert db.query(SourcePost).count() == 26
    assert db.query(PostVersion).count() == 26
    assert db.query(PostMedia).count() == 6


def test_edit_deletion_replies_albums_and_window_semantics(db, demo_channel, owner):
    import_fixture(db, demo_channel, owner, "telegram_export_sample.json")
    first = db.query(SourcePost).filter_by(telegram_message_id=101).one()
    original_version = first.version

    payload = load_json(FIXTURES / "telegram_export_sample.json")
    payload["messages"][0]["text"] = "Corrected text: the clearance operation ended on Tuesday morning."
    from channel_press.ingestion.service import import_payload
    from channel_press.auth import active_rights

    stats = import_payload(
        db,
        demo_channel,
        payload,
        imported_by=owner.id,
        media_root=FIXTURES / "media",
        rights_grant=active_rights(db, demo_channel.id),
    )
    db.commit()
    assert stats.updated == 1
    first = db.query(SourcePost).filter_by(telegram_message_id=101).one()
    assert first.version == original_version + 1
    assert "Corrected text" in first.text
    assert (
        db.query(PostVersion)
        .filter(PostVersion.post_id == first.id)
        .count()
        == original_version + 1
    )

    deleted = db.query(SourcePost).filter_by(telegram_message_id=904).one()
    apply_deletion(db, deleted, reason="withdrawn by source")
    db.commit()
    assert deleted.inclusion_status == "excluded"
    assert deleted.exclusion_reason == "withdrawn by source"
    assert deleted.deleted_at is not None

    edges = list(db.query(ReplyEdge))
    assert any(edge.edge_type == "reply" for edge in edges)
    post_102 = db.query(SourcePost).filter_by(telegram_message_id=102).one()
    assert post_102.reply_to_message_id == 101

    album_posts = db.query(SourcePost).filter(SourcePost.grouped_id == 9001).all()
    assert len(album_posts) >= 2

    start = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
    end = datetime(2026, 10, 20, 7, 0, tzinfo=UTC)
    window = source_window(db, demo_channel.id, start, end)
    assert window
    assert all(start <= post.posted_at < end for post in window)
    assert all(post.inclusion_status == "included" for post in window)


def test_media_path_traversal_is_rejected(db, demo_channel, owner):
    from channel_press.auth import active_rights
    from channel_press.ingestion.service import import_payload

    payload = {
        "messages": [
            {
                "id": 1001,
                "date": "2026-10-20T08:00:00Z",
                "text": "A report with a suspicious media reference.",
                "media": [{"kind": "photo", "file": "../outside.png"}],
            }
        ]
    }
    stats = import_payload(
        db,
        demo_channel,
        payload,
        imported_by=owner.id,
        media_root=FIXTURES / "media",
        rights_grant=active_rights(db, demo_channel.id),
    )
    db.commit()
    assert stats.media == 0
    assert db.query(PostMedia).count() == 0


def test_live_channel_without_rights_is_excluded_from_processing(db, owner):
    from channel_press.auth import active_rights
    from channel_press.ingestion.service import import_payload

    channel = Channel(slug="uncleared-source", title="Uncleared source", is_public=False, is_demo=False)
    db.add(channel)
    db.flush()
    payload = {
        "messages": [
            {
                "id": 1,
                "date": "2026-10-20T08:00:00Z",
                "text": "A report that should not be processed without a rights declaration.",
            }
        ]
    }
    assert active_rights(db, channel.id) is None
    stats = import_payload(db, channel, payload, imported_by=owner.id)
    db.commit()
    assert stats.accepted == 0
    assert stats.rejected == 1
    post = db.query(SourcePost).filter_by(channel_id=channel.id).one()
    assert post.exclusion_reason == "rights_unconfirmed"


def test_public_channel_source_url_is_built_from_handle(db, owner):
    from channel_press.auth import active_rights
    from channel_press.ingestion.service import import_payload
    from channel_press.models import RightsGrant

    channel = Channel(slug="public-source", title="Public source", handle="@public_source", is_public=True, is_demo=False)
    db.add(channel)
    db.flush()
    grant = RightsGrant(
        channel_id=channel.id,
        grantor_name="Owner",
        authority_basis="Documented permission",
        scope_json='["ingest", "ai_process", "publish"]',
        created_by=owner.id,
    )
    db.add(grant)
    db.flush()
    payload = {"messages": [{"id": 77, "date": "2026-10-20T08:00:00Z", "text": "Police confirmed a roadside bomb struck a patrol in Quetta."}]}
    import_payload(db, channel, payload, imported_by=owner.id, rights_grant=active_rights(db, channel.id))
    db.commit()
    post = db.query(SourcePost).filter_by(channel_id=channel.id).one()
    assert post.source_url == "https://t.me/public_source/77"
    assert post.inclusion_status == "included"
