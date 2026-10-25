"""Phase A regression tests: revisioned editing, previews and immutable versions.

These tests encode the editorial contract that was previously broken:

* a saved article edit is what the next rendered preview contains;
* a published version is a frozen row plus a frozen bundle, and later edits
  create a new version instead of mutating it;
* every article in an edition is editable, not only review-flagged ones;
* a stale form cannot overwrite a newer saved revision.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import fitz
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select

from channel_press import models  # noqa: F401  (register tables)
from channel_press.editorial.revisions import (
    article_snapshot,
    revision_history,
    set_article_status,
    snapshot_hash,
    working_revision,
)
from channel_press.media.service import apply_media_choice, rank_media
from channel_press.models import Article, Edition, EditionArticle, ReviewFlag
from channel_press.pipeline import PipelineRequest, run_pipeline
from channel_press.publishing.service import render_preview
from channel_press.timeutil import utcnow
from tests.conftest import import_fixture

WINDOW = dict(
    window_start=datetime(2026, 10, 18, tzinfo=UTC),
    window_end=datetime(2026, 10, 22, tzinfo=UTC),
    cutoff_at=datetime(2026, 10, 21, tzinfo=UTC),
)


def _pdf_text(path: str | Path) -> str:
    with fitz.open(str(path)) as document:
        return "\n".join(page.get_text("text") for page in document)


def _sha(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _seed(db, channel, owner) -> Edition:
    import_fixture(db, channel, owner, "telegram_export_sample.json")
    result = run_pipeline(
        db,
        PipelineRequest(
            channel=channel,
            target_release_at=utcnow(),
            kind="manual",
            title="Workspace regression issue",
            actor_id=owner.id,
            publish=True,
            mark_published=True,
            demo_edition=True,
            **WINDOW,
        ),
    )
    db.commit()
    return result.edition


def _first_link(db, edition: Edition) -> tuple[EditionArticle, Article]:
    link = db.scalar(
        select(EditionArticle)
        .where(EditionArticle.edition_id == edition.id)
        .order_by(EditionArticle.sort_order.asc(), EditionArticle.id.asc())
    )
    assert link is not None
    article = db.get(Article, link.article_id)
    assert article is not None
    return link, article


def _approve_open_flags(db, edition: Edition) -> None:
    """Simulate the editor clearing the fixture's blocking review items."""
    article_ids = list(
        db.scalars(
            select(EditionArticle.article_id).where(EditionArticle.edition_id == edition.id)
        )
    )
    for flag in db.scalars(
        select(ReviewFlag).where(
            ReviewFlag.status == "open",
            ReviewFlag.article_id.in_(article_ids or [-1]),
        )
    ):
        flag.status = "approved"
        flag.decision = "Accepted by test."
        flag.resolved_at = utcnow()
    db.commit()


def _save(client, csrf: str, edition: Edition, article: Article, **overrides):
    data = {
        "csrf_token": csrf,
        "expected_revision": str(article.revision),
        "title": article.title,
        "deck": article.deck,
        "standfirst": article.standfirst,
        "body_text": article.body_text,
        "byline": article.byline,
        "section": article.section,
        "genre": article.genre,
    }
    data.update(overrides)
    response = client.post(
        f"/editions/{edition.id}/articles/{article.id}/save",
        data=data,
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    return response.headers["location"]


def test_edit_saves_revision_preview_and_immutable_versions(db, demo_channel, owner, client, auth):
    edition = _seed(db, demo_channel, owner)
    link_v1, article = _first_link(db, edition)
    v1_pdf = Path(edition.pdf_path)
    v1_hash = _sha(v1_pdf)
    v1_snapshot = json.loads(link_v1.snapshot_json)
    assert v1_snapshot["title"] == article.title
    v1_revision = article.revision

    # The workspace renders every article, and the first article has an editor.
    page = client.get(f"/editions/{edition.id}?article={article.id}")
    assert page.status_code == 200
    assert "Save and Update Preview" in page.text

    sentinel = "ZZ EDITOR SENTINEL 4711"
    body = "ZZ EDITOR BODY SENTINEL 4711 appears here.\n\nA second edited paragraph."
    location = _save(
        client,
        auth,
        edition,
        article,
        title=sentinel,
        body_text=body,
    )
    # Saving a published version opens a working revision and redirects to it.
    working_id = int(location.split("/editions/")[1].split("?")[0])
    assert working_id != edition.id
    db.expire_all()
    working = db.get(Edition, working_id)
    edited = db.get(Article, article.id)
    assert working.version == edition.version + 1
    assert working.status != "published"
    assert edited.revision == v1_revision + 1
    assert edited.title == sentinel

    # The preview bundle is the edited text, not the V1 text.
    assert working.preview_pdf_path and Path(working.preview_pdf_path).exists()
    preview_text = _pdf_text(working.preview_pdf_path)
    assert "4711" in preview_text
    assert working.preview_state == "current"

    # V1 is byte-identical and its database row/manifest/snapshot are unchanged.
    original = db.get(Edition, edition.id)
    assert original.status == "published"
    assert original.version == 1
    assert Path(original.pdf_path) == v1_pdf
    assert _sha(v1_pdf) == v1_hash
    assert "4711" not in _pdf_text(v1_pdf)
    frozen = db.scalar(
        select(EditionArticle).where(
            EditionArticle.edition_id == original.id,
            EditionArticle.article_id == article.id,
        )
    )
    assert frozen is not None
    assert frozen.article_revision == v1_revision
    assert json.loads(frozen.snapshot_json) == v1_snapshot
    assert json.loads(original.manifest_json)["pdf"]["sha256"] == v1_hash

    # The fixture carries genuine open review flags. An editor clears them
    # before releasing the revised version (the release gate is intentionally
    # strict), then releases V2.
    _approve_open_flags(db, working)
    response = client.post(
        f"/editions/{working.id}/publish",
        data={"csrf_token": auth},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.expire_all()
    published_v2 = db.get(Edition, working.id)
    assert published_v2.status == "published", response.headers.get("location")
    assert "4711" in _pdf_text(published_v2.pdf_path)
    assert _sha(v1_pdf) == v1_hash
    assert "4711" not in _pdf_text(v1_pdf)
    v2_hash = _sha(published_v2.pdf_path)
    v2_snapshot_hash = snapshot_hash(
        json.loads(
            db.scalar(
                select(EditionArticle).where(
                    EditionArticle.edition_id == published_v2.id,
                    EditionArticle.article_id == article.id,
                )
            ).snapshot_json
        )
    )

    # A later edit opens V3 and leaves V2 exactly as released.
    location = _save(
        client,
        auth,
        published_v2,
        edited,
        title="ZZ THIRD VERSION 9911",
        body_text="Third version body 9911.",
    )
    v3_id = int(location.split("/editions/")[1].split("?")[0])
    assert v3_id not in {edition.id, working.id}
    db.expire_all()
    still_v2 = db.get(Edition, working.id)
    assert still_v2.status == "published"
    assert still_v2.manifest_json == published_v2.manifest_json
    assert _sha(still_v2.pdf_path) == v2_hash
    frozen_v2 = db.scalar(
        select(EditionArticle).where(
            EditionArticle.edition_id == still_v2.id,
            EditionArticle.article_id == article.id,
        )
    )
    assert snapshot_hash(json.loads(frozen_v2.snapshot_json)) == v2_snapshot_hash
    v3 = db.get(Edition, v3_id)
    assert v3.version == 3


def test_stale_form_cannot_overwrite_newer_revision(db, demo_channel, owner, client, auth):
    edition = _seed(db, demo_channel, owner)
    _, article = _first_link(db, edition)
    stale_revision = article.revision

    _save(client, auth, edition, article, title="First accepted save", body_text="First body.")
    db.expire_all()
    working = db.scalar(
        select(Edition)
        .where(Edition.channel_id == edition.channel_id, Edition.version == 2)
        .limit(1)
    )
    assert working is not None
    current = db.get(Article, article.id)
    assert current.revision == stale_revision + 1

    response = client.post(
        f"/editions/{working.id}/articles/{article.id}/save",
        data={
            "csrf_token": auth,
            "expected_revision": str(stale_revision),
            "title": "Stale overwrite",
            "body_text": "Stale body.",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "err=" in response.headers["location"]
    db.expire_all()
    unchanged = db.get(Article, article.id)
    assert unchanged.title == "First accepted save"
    assert unchanged.revision == stale_revision + 1


def test_every_article_in_edition_is_editable(db, demo_channel, owner, client, auth):
    edition = _seed(db, demo_channel, owner)
    links = list(
        db.scalars(
            select(EditionArticle)
            .where(EditionArticle.edition_id == edition.id)
            .order_by(EditionArticle.sort_order)
        )
    )
    assert len(links) >= 2
    for link in links:
        article = db.get(Article, link.article_id)
        page = client.get(f"/editions/{edition.id}?article={article.id}")
        assert page.status_code == 200
        assert "Save and Update Preview" in page.text
        assert article.title in page.text


def test_excluded_article_is_not_composed_and_held_blocks_release(
    db, demo_channel, owner, client, auth
):
    published = _seed(db, demo_channel, owner)
    working = working_revision(db, published, actor_id=owner.id)
    db.commit()
    _approve_open_flags(db, working)
    _, article = _first_link(db, working)
    assert article.status not in {"held", "excluded"}
    other = db.scalar(
        select(EditionArticle).where(
            EditionArticle.edition_id == working.id,
            EditionArticle.article_id != article.id,
        )
    )
    assert other is not None, "the fixture must leave a printable article after exclusion"

    set_article_status(db, article, "excluded", actor_id=owner.id)
    db.commit()
    render_preview(db, working)
    db.commit()
    text = _pdf_text(working.preview_pdf_path)
    assert article.title not in text

    set_article_status(db, article, "held", actor_id=owner.id)
    db.commit()
    response = client.post(
        f"/editions/{working.id}/publish",
        data={"csrf_token": auth},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "err=" in response.headers["location"]
    assert "Held" in response.headers["location"] or "held" in response.headers["location"]
    db.expire_all()
    assert db.get(Edition, working.id).status != "published"


def test_revision_history_records_field_level_diffs(db, demo_channel, owner, client, auth):
    edition = _seed(db, demo_channel, owner)
    _, article = _first_link(db, edition)
    _save(client, auth, edition, article, title="A concrete new headline", body_text="One.\n\nTwo.")
    db.expire_all()
    working = db.scalar(
        select(Edition)
        .where(Edition.channel_id == edition.channel_id, Edition.version == 2)
        .limit(1)
    )
    current = db.get(Article, article.id)
    _save(client, auth, working, current, title="A sharper final headline", body_text="One.\n\nThree.")
    db.expire_all()
    latest = db.get(Article, article.id)
    history = revision_history(db, latest)
    assert history[0]["revision"] == latest.revision
    fields = {change["field"] for change in history[0]["diff"]}
    assert {"title", "body"} <= fields
    assert any(
        change["before"] == "A concrete new headline" and change["after"] == "A sharper final headline"
        for change in history[0]["diff"]
        if change["field"] == "title"
    )


def test_editor_can_replace_lead_media_with_eligible_source_asset(db, demo_channel, owner):
    edition = _seed(db, demo_channel, owner)
    _, article = _first_link(db, edition)
    story = db.get(models.StoryCluster, article.story_id)
    assert story is not None
    choices = rank_media(db, story)
    if len(choices) < 2:
        pytest.skip("fixture story has fewer than two eligible media assets")
    original = article_snapshot(db, article)["media"]
    original_ids = {row["media_id"] for row in original}
    replacement = next(
        (choice for choice in choices if choice.media.id not in original_ids),
        choices[-1],
    )
    from channel_press.config import settings

    record = apply_media_choice(
        db,
        article_id=article.id,
        story=story,
        media_id=replacement.media.id,
        target_dir=settings.media_dir,
        caption="Editor-selected replacement caption.",
        credit="Fixture source",
    )
    db.commit()
    assert record.media_id == replacement.media.id
    assert Path(record.path).exists()
    snapshot = article_snapshot(db, article)
    leads = [row for row in snapshot["media"] if row["role"] == "lead"]
    assert len(leads) == 1
    assert leads[0]["media_id"] == replacement.media.id
    assert leads[0]["caption"] == "Editor-selected replacement caption."


def test_alembic_migration_creates_workspace_columns(tmp_path, monkeypatch):
    from channel_press.config import settings

    database = tmp_path / "migration.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{database.as_posix()}")
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    inspector = inspect(engine)
    article_columns = {column["name"] for column in inspector.get_columns("articles")}
    edition_columns = {column["name"] for column in inspector.get_columns("editions")}
    link_columns = {column["name"] for column in inspector.get_columns("edition_articles")}
    assert "byline" in article_columns
    assert {
        "preview_state",
        "preview_revision",
        "preview_updated_at",
        "preview_dir",
        "preview_pdf_path",
    } <= edition_columns
    assert {"article_revision", "snapshot_json"} <= link_columns
    engine.dispose()


def test_workspace_assets_are_served(client):
    js = client.get("/static/js/workspace.js")
    assert js.status_code == 200
    assert "beforeunload" in js.text
    css = client.get("/static/css/app.css")
    assert css.status_code == 200
    assert ".workspace" in css.text
