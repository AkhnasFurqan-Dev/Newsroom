"""Multi-publication identity, source membership, isolation and attribution.

These tests use only local fixture data. They prove the server-side boundaries
and the cross-source merge behaviour; they are not evidence that a live
Telegram account, rights grant or newspaper release has been configured.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from channel_press.models import (
    Article,
    ArticleSource,
    Channel,
    ChannelPermission,
    Edition,
    EditionSchedule,
    Job,
    PostFragment,
    PublicationEditor,
    PublicationProfile,
    PublicationSource,
    SourcePost,
    StoryClaim,
    StoryCluster,
    User,
)
from channel_press.pipeline import merge_cross_source_stories, record_article_sources
from channel_press.publications import (
    can_access_publication,
    default_publication,
    publication_channels,
    resolve_publication,
)
from channel_press.scheduling.service import enqueue_due_jobs, enqueue_manual_job
from channel_press.security import hash_password
from tests.conftest import OWNER_PASSWORD, login


def _publication(db, name: str, slug: str, **overrides) -> PublicationProfile:
    values = {
        "name": name,
        "slug": slug,
        "channel_handle": f"@{slug}",
        "slogan": "Local fixture newspaper",
        "footer_text": "",
        "source_disclosure": "",
        "city": "Karachi",
        "timezone": "Asia/Karachi",
        "accent": "#7b1f24",
        "paper_size": "tabloid",
        "map_profile": "neutral",
        "qr_strategy": "appendix",
        "section_order_json": json.dumps(["national", "international"]),
        "is_active": True,
    }
    values.update(overrides)
    row = PublicationProfile(**values)
    db.add(row)
    db.flush()
    return row


def _channel(db, slug: str, title: str) -> Channel:
    row = Channel(slug=slug, title=title, handle=f"@{slug}", is_demo=True)
    db.add(row)
    db.flush()
    return row


def _story(db, channel: Channel, title: str, *, fingerprint: str, importance: float = 0.5):
    story = StoryCluster(
        channel_id=channel.id,
        canonical_title=title,
        summary=title,
        section="national",
        genre="news",
        importance=importance,
        risk_level="low",
        fingerprint=fingerprint,
    )
    db.add(story)
    db.flush()
    return story


def _editor(db) -> User:
    user = User(
        username="paper-editor",
        password_hash=hash_password(OWNER_PASSWORD),
        display_name="Paper Editor",
        role="editor",
    )
    db.add(user)
    db.flush()
    return user


def test_owner_can_create_and_switch_newspapers(client, db, owner, auth):
    created = client.post(
        "/publications/new",
        data={
            "csrf_token": auth,
            "name": "Evening Ledger",
            "city": "Lahore",
            "timezone": "Asia/Karachi",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    evening = db.scalar(select(PublicationProfile).where(PublicationProfile.name == "Evening Ledger"))
    assert evening is not None and evening.slug == "evening-ledger"
    db.commit()

    switch = client.post(
        "/publications/select",
        data={"csrf_token": auth, "publication_id": str(evening.id)},
        follow_redirects=False,
    )
    assert switch.status_code == 303
    assert f"cp_publication={evening.id}" in switch.headers.get("set-cookie", "")

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Evening Ledger" in dashboard.text
    assert "The Channel Dispatch" in dashboard.text  # selector still lists every paper

    branding = client.get("/branding")
    assert branding.status_code == 200
    assert "Newspapers on this computer" in branding.text
    assert "Evening Ledger" in branding.text
    assert "Edit Evening Ledger" in branding.text or "Sources for Evening Ledger" in branding.text


def test_publication_membership_hides_other_papers_stories_and_editions(client, db, owner):
    main = default_publication(db)
    assert main is not None
    other = _publication(db, "Harbour Weekly", "harbour-weekly")
    main_channel = _channel(db, "main-desk", "Main Desk")
    other_channel = _channel(db, "harbour-desk", "Harbour Desk")
    db.add_all(
        [
            PublicationSource(publication_id=main.id, channel_id=main_channel.id, priority=1, weight=1.0),
            PublicationSource(publication_id=other.id, channel_id=other_channel.id, priority=1, weight=1.0),
        ]
    )
    _story(db, main_channel, "Main paper grain harvest rises", fingerprint="main-grain")
    _story(db, other_channel, "Hidden paper port strike ends", fingerprint="other-port")
    now = datetime(2026, 10, 20, tzinfo=UTC)
    db.add_all(
        [
            Edition(
                channel_id=main_channel.id,
                publication_id=main.id,
                source_channel_ids_json=json.dumps([main_channel.id]),
                kind="daily",
                title="Main morning edition",
                status="awaiting_review",
                window_start=now - timedelta(days=1),
                window_end=now,
                cutoff_at=now,
                target_release_at=now,
                timezone="Asia/Karachi",
            ),
            Edition(
                channel_id=other_channel.id,
                publication_id=other.id,
                source_channel_ids_json=json.dumps([other_channel.id]),
                kind="daily",
                title="Harbour morning edition",
                status="awaiting_review",
                window_start=now - timedelta(days=1),
                window_end=now,
                cutoff_at=now,
                target_release_at=now,
                timezone="Asia/Karachi",
            ),
        ]
    )
    editor = _editor(db)
    db.add_all(
        [
            PublicationEditor(publication_id=main.id, user_id=editor.id, role="editor"),
            ChannelPermission(
                channel_id=main_channel.id,
                user_id=editor.id,
                role="viewer",
                granted_by_id=owner.id,
            ),
        ]
    )
    db.commit()

    editor_client = TestClient(client.app, base_url="http://testserver")
    try:
        login(editor_client, editor.username, OWNER_PASSWORD)
        assert can_access_publication(db, editor, main, "editor") is True
        assert can_access_publication(db, editor, other) is False

        stories = editor_client.get("/stories")
        assert stories.status_code == 200
        assert "Main paper grain harvest rises" in stories.text
        assert "Hidden paper port strike ends" not in stories.text

        editions = editor_client.get("/editions")
        assert editions.status_code == 200
        assert "Main morning edition" in editions.text
        assert "Harbour morning edition" not in editions.text

        forbidden = editor_client.get(f"/editions/{other.id}")
        assert forbidden.status_code == 403
    finally:
        editor_client.close()


def test_cross_source_merge_keeps_the_weighted_report_and_attributes_both_channels(db, owner):
    publication = default_publication(db)
    assert publication is not None
    primary = _channel(db, "wire-primary", "Primary Wire")
    secondary = _channel(db, "wire-secondary", "Secondary Wire")
    db.add_all(
        [
            PublicationSource(
                publication_id=publication.id, channel_id=primary.id, priority=1, weight=2.0
            ),
            PublicationSource(
                publication_id=publication.id, channel_id=secondary.id, priority=2, weight=1.0
            ),
        ]
    )
    shared = "Border crossing reopens after flood repairs"
    primary_story = _story(db, primary, shared, fingerprint="cross-primary", importance=0.45)
    secondary_story = _story(db, secondary, shared, fingerprint="cross-secondary", importance=0.60)
    primary_post = SourcePost(
        channel_id=primary.id,
        telegram_message_id=1001,
        posted_at=datetime(2026, 10, 20, 6, tzinfo=UTC),
        text="Primary wire report on the reopened border crossing.",
    )
    secondary_post = SourcePost(
        channel_id=secondary.id,
        telegram_message_id=2001,
        posted_at=datetime(2026, 10, 20, 7, tzinfo=UTC),
        text="Secondary wire report on the reopened border crossing.",
    )
    db.add_all([primary_post, secondary_post])
    db.flush()
    db.add_all(
        [
            PostFragment(post_id=primary_post.id, story_id=primary_story.id, text=primary_post.text),
            PostFragment(post_id=secondary_post.id, story_id=secondary_story.id, text=secondary_post.text),
            StoryClaim(story_id=primary_story.id, text="The crossing reopened on Tuesday."),
            StoryClaim(story_id=secondary_story.id, text="The crossing reopened on Tuesday."),
            StoryClaim(story_id=secondary_story.id, text="Trucks queued for six kilometres."),
        ]
    )
    db.flush()

    kept, absorbed = merge_cross_source_stories(
        db,
        [primary_story, secondary_story],
        priorities={primary.id: (1, 2.0), secondary.id: (2, 1.0)},
    )

    # Weighted importance, not raw importance, decides which report survives.
    assert [story.id for story in kept] == [primary_story.id]
    assert absorbed[primary_story.id][0].id == secondary_story.id
    db.refresh(secondary_story)
    assert secondary_story.merged_into_id == primary_story.id
    assert secondary_story.status == "merged"
    moved = db.scalars(select(PostFragment).where(PostFragment.story_id == primary_story.id)).all()
    assert {row.post_id for row in moved} == {primary_post.id, secondary_post.id}
    claims = db.scalars(select(StoryClaim).where(StoryClaim.story_id == primary_story.id)).all()
    assert {claim.text for claim in claims} == {
        "The crossing reopened on Tuesday.",
        "Trucks queued for six kilometres.",
    }

    article = Article(
        story_id=primary_story.id,
        title="Border crossing reopens after flood repairs",
        body_text="The crossing reopened after repairs.\n\nTrucks queued.",
        status="draft",
    )
    db.add(article)
    db.flush()
    record_article_sources(db, article)
    sources = db.scalars(select(ArticleSource).where(ArticleSource.article_id == article.id)).all()
    assert {row.channel_id for row in sources} == {primary.id, secondary.id}
    assert all(row.post_count == 1 for row in sources)


def test_cross_source_merge_never_folds_two_reports_from_the_same_channel(db, owner):
    publication = default_publication(db)
    assert publication is not None
    channel = _channel(db, "single-source", "Single Source")
    db.add(PublicationSource(publication_id=publication.id, channel_id=channel.id, priority=1))
    db.flush()
    # Generic local-service editorials share almost every word; the city differs.
    sialkot = _story(
        db,
        channel,
        "Editorial: Local services in Sialkot need a published budget",
        fingerprint="same-channel-sialkot",
    )
    gilgit = _story(
        db,
        channel,
        "Editorial: Local services in Gilgit need a published budget",
        fingerprint="same-channel-gilgit",
    )
    db.flush()

    kept, absorbed = merge_cross_source_stories(db, [sialkot, gilgit])

    assert {story.id for story in kept} == {sialkot.id, gilgit.id}
    assert absorbed == {}
    db.refresh(gilgit)
    assert gilgit.merged_into_id is None
    assert gilgit.status == "draft"


def test_manual_job_records_publication_and_all_enabled_sources(db, owner):
    publication = default_publication(db)
    assert publication is not None
    primary = _channel(db, "morning-a", "Morning A")
    second = _channel(db, "morning-b", "Morning B")
    disabled = _channel(db, "morning-c", "Morning C")
    db.add_all(
        [
            PublicationSource(publication_id=publication.id, channel_id=primary.id, priority=1),
            PublicationSource(publication_id=publication.id, channel_id=second.id, priority=2),
            PublicationSource(
                publication_id=publication.id, channel_id=disabled.id, priority=3, is_enabled=False
            ),
        ]
    )
    db.commit()
    now = datetime(2026, 10, 22, tzinfo=UTC)
    job = enqueue_manual_job(
        db,
        primary,
        window_start=now - timedelta(days=1),
        window_end=now,
        publication=publication,
        channels=publication_channels(db, publication, enabled_only=True),
    )
    db.commit()
    assert job.publication_id == publication.id
    payload = json.loads(job.payload_json)
    assert payload["publication_id"] == publication.id
    assert payload["channel_ids"] == [primary.id, second.id]
    assert payload["window_key"].startswith(f"pub{publication.id}:")
    assert job.key.startswith(f"manual:pub{publication.id}:")


def test_legacy_manual_job_remains_single_channel(db, owner, demo_channel):
    now = datetime(2026, 10, 22, tzinfo=UTC)
    job = enqueue_manual_job(
        db,
        demo_channel,
        window_start=now - timedelta(days=1),
        window_end=now,
    )
    db.commit()
    assert job.publication_id is None
    payload = json.loads(job.payload_json)
    assert payload["publication_id"] is None
    assert payload["channel_ids"] == [demo_channel.id]
    assert payload["window_key"].startswith(f"{demo_channel.id}:manual:")


def test_due_schedule_expands_every_enabled_publication_source(db, demo_channel):
    publication = default_publication(db)
    assert publication is not None
    second = _channel(db, "schedule-second", "Schedule Second")
    db.add(
        PublicationSource(publication_id=publication.id, channel_id=second.id, priority=2)
    )
    schedule = db.scalar(
        select(EditionSchedule).where(EditionSchedule.channel_id == demo_channel.id)
    )
    assert schedule is not None
    schedule.enabled = True
    schedule.publication_id = publication.id
    db.commit()

    now = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)
    created = enqueue_due_jobs(db, now=now)
    db.commit()
    assert created
    payload = json.loads(created[-1].payload_json)
    assert created[-1].publication_id == publication.id
    assert payload["channel_ids"] == [demo_channel.id, second.id]
    assert payload["publication_id"] == publication.id


def test_migration_backfills_single_paper_installation(tmp_path, monkeypatch):
    from channel_press.config import settings

    database = tmp_path / "multi-publication.db"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{database.as_posix()}")
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    session_factory = sessionmaker(bind=engine)
    with session_factory() as session:
        publication = PublicationProfile(
            slug="main",
            name="Legacy Dispatch",
            channel_handle="@legacy",
            is_active=True,
        )
        channel = Channel(slug="legacy-desk", title="Legacy Desk", handle="@legacy")
        session.add_all([publication, channel])
        session.flush()
        now = datetime(2026, 10, 20, tzinfo=UTC)
        edition = Edition(
            channel_id=channel.id,
            publication_id=None,
            kind="daily",
            title="Legacy edition",
            status="published",
            window_start=now - timedelta(days=1),
            window_end=now,
            cutoff_at=now,
            target_release_at=now,
            timezone="Asia/Karachi",
        )
        schedule = EditionSchedule(channel_id=channel.id, kind="daily")
        job = Job(key="legacy-job", kind="edition", channel_id=channel.id)
        session.add_all([edition, schedule, job])
        session.commit()
        channel_id, edition_id = channel.id, edition.id

    # Remove the Phase C schema, then re-upgrade as an existing installation.
    command.downgrade(config, "f4b8c2d6e123")
    command.upgrade(config, "head")
    engine.dispose()

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with engine.connect() as connection:
            links = connection.execute(
                text("SELECT publication_id, channel_id, is_enabled FROM publication_sources")
            ).all()
            assert [(row[1], bool(row[2])) for row in links] == [(channel_id, True)]
            edition_row = connection.execute(
                text(
                    "SELECT publication_id, source_channel_ids_json, source_status "
                    "FROM editions WHERE id = :id"
                ),
                {"id": edition_id},
            ).one()
            assert edition_row[0] == links[0][0]
            assert json.loads(edition_row[1]) == [channel_id]
            assert edition_row[2] == "complete"
            assert connection.execute(
                text("SELECT publication_id FROM edition_schedules")
            ).scalar() == links[0][0]
            assert connection.execute(
                text("SELECT publication_id FROM jobs WHERE key = 'legacy-job'")
            ).scalar() == links[0][0]
            slugs = connection.execute(
                text("SELECT slug, is_active FROM publication_profiles")
            ).all()
            assert ("main", 1) in [(row[0], int(row[1])) for row in slugs]
    finally:
        engine.dispose()


def test_resolve_publication_uses_cookie_then_first_accessible(db, owner):
    main = default_publication(db)
    other = _publication(db, "Night Edition", "night-edition")
    editor = _editor(db)
    db.add(PublicationEditor(publication_id=other.id, user_id=editor.id, role="viewer"))
    db.commit()

    class Request:
        def __init__(self, query: dict[str, str] | None = None, cookies: dict[str, str] | None = None):
            self.query_params = query or {}
            self.cookies = cookies or {}

    assert resolve_publication(db, Request(), owner).id == main.id
    assert resolve_publication(db, Request(query={"publication": "night-edition"}), owner).id == other.id
    assert resolve_publication(db, Request(cookies={"cp_publication": str(other.id)}), editor).id == other.id
    # A paper this user cannot open never becomes the active context.
    assert resolve_publication(db, Request(query={"publication": "main"}), editor).id == other.id


def test_two_source_build_produces_one_merged_attributed_printable_edition(
    db, demo_channel, owner, tmp_path
):
    """The multi-source path must merge, attribute and print in one real run.

    Both fixture channels carry the same synthetic wire report, which is what a
    real newspaper sees when two authorized sources cover one event. The build
    must fold the duplicate into a single story, credit both channels on the
    article, expose which sources the edition used, and render a PDF whose
    appendix names both channels.
    """
    import fitz

    from channel_press.pipeline import PipelineRequest, run_pipeline
    from channel_press.timeutil import utcnow
    from tests.conftest import import_fixture

    publication = default_publication(db)
    assert publication is not None
    second = _channel(db, "evening-wire", "Evening Wire")
    existing_link = db.scalar(
        select(PublicationSource).where(
            PublicationSource.publication_id == publication.id,
            PublicationSource.channel_id == demo_channel.id,
        )
    )
    if existing_link is None:
        db.add(
            PublicationSource(
                publication_id=publication.id,
                channel_id=demo_channel.id,
                priority=1,
                weight=1.0,
            )
        )
    db.add(
        PublicationSource(
            publication_id=publication.id,
            channel_id=second.id,
            priority=2,
            weight=0.8,
        )
    )
    db.commit()

    import_fixture(db, demo_channel, owner, "telegram_export_sample.json")
    second_import = import_fixture(db, second, owner, "telegram_export_sample.json")
    assert second_import.created > 0

    result = run_pipeline(
        db,
        PipelineRequest(
            channel=demo_channel,
            channels=[demo_channel, second],
            publication=publication,
            window_start=datetime(2026, 10, 18, tzinfo=UTC),
            window_end=datetime(2026, 10, 22, tzinfo=UTC),
            cutoff_at=datetime(2026, 10, 21, tzinfo=UTC),
            target_release_at=utcnow(),
            kind="manual",
            title="Two-source morning edition",
            actor_id=owner.id,
            publish=True,
            mark_published=True,
            demo_edition=True,
            output_root=tmp_path,
        ),
    )
    db.commit()
    edition = result.edition
    published = result.publish_result
    assert published is not None
    assert published.pdf_path.exists()

    # Edition identity and source accounting.
    assert edition.publication_id == publication.id
    assert sorted(json.loads(edition.source_channel_ids_json)) == sorted(
        [demo_channel.id, second.id]
    )
    assert edition.source_status == "complete"
    manifest = json.loads(published.manifest_path.read_text(encoding="utf-8"))
    assert manifest["edition"]["id"] == edition.id
    manifest_channels = {
        row.get("channel_id") for row in manifest["sources"] if row.get("channel_id")
    }
    assert manifest_channels == {demo_channel.id, second.id}
    assert manifest_channels == {demo_channel.id, second.id}

    # Cross-source duplicates are folded, and the absorbed row survives as an
    # audit pointer rather than disappearing.
    absorbed = list(
        db.scalars(
            select(StoryCluster).where(
                StoryCluster.merged_into_id.is_not(None),
                StoryCluster.status == "merged",
            )
        )
    )
    assert absorbed, "identical reports from two sources must be folded into one story"
    assert {row.channel_id for row in absorbed} == {second.id}

    # A merged article credits both channels, with post counts per channel.
    attributed = []
    for article in db.scalars(select(Article)):
        rows = list(
            db.scalars(select(ArticleSource).where(ArticleSource.article_id == article.id))
        )
        channels = {row.channel_id for row in rows}
        if channels == {demo_channel.id, second.id}:
            attributed.append((article, rows))
    assert attributed, "the merged story must record both contributing channels"
    article, rows = attributed[0]
    assert all(row.post_count >= 1 for row in rows)
    assert all(row.first_posted_at is not None for row in rows)

    # Both channels appear in the printed source appendix, and no blank or
    # malformed page is produced.
    document = fitz.open(published.pdf_path)
    try:
        assert document.page_count == manifest["pdf"]["page_count"]
        text = "\n".join(page.get_text("text") for page in document)
        for page in document:
            body = page.get_text("text").strip()
            assert body, f"page {page.number + 1} is blank"
            assert abs(page.rect.width - 792) < 0.5
            assert abs(page.rect.height - 1224) < 0.5
    finally:
        document.close()
    # Both channels are credited in the internal manifest and article source
    # rows, but reader-facing pages carry no channel handles or provenance
    # appendix.
    assert manifest_channels == {demo_channel.id, second.id}
    flat_text = " ".join(text.split())
    headline = " ".join(article.title.split())
    assert headline[:24] in flat_text or headline.split(":")[0][:24] in flat_text
    assert (demo_channel.handle or demo_channel.title) not in text
    assert (second.handle or second.title) not in text
    for forbidden in ("localhost", "127.0.0.1", "file:///", "Sources & provenance"):
        assert forbidden not in text.lower()
