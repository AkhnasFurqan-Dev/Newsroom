from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta


from channel_press.models import (
    Article,
    Edition,
    EditionArticle,
    EditionPage,
    EditionSchedule,
    Job,
    ReviewFlag,
    StoryCluster,
)
from channel_press.scheduling.service import (
    WorkerBusy,
    WorkerLease,
    _progress_value,
    claim_job,
    due_cutoffs,
    enqueue_due_jobs,
    enqueue_manual_job,
    finalize_auto_publish,
    release_expired_locks,
)
from channel_press.publishing.service import edition_review_blockers


def _schedule(db, channel, **overrides):
    schedule = db.query(EditionSchedule).filter_by(channel_id=channel.id, kind="daily").one()
    schedule.enabled = True
    schedule.requires_review = overrides.pop("requires_review", True)
    schedule.auto_publish_nonrisk = overrides.pop("auto_publish_nonrisk", False)
    for key, value in overrides.items():
        setattr(schedule, key, value)
    db.commit()
    return schedule


def _edition(db, channel, *, status="awaiting_review", release_at=None):
    now = datetime(2026, 10, 22, tzinfo=UTC)
    edition = Edition(
        channel_id=channel.id,
        kind="manual",
        title="Test edition",
        status=status,
        window_start=now - timedelta(days=1),
        window_end=now,
        cutoff_at=now,
        target_release_at=release_at or now,
        timezone="Asia/Karachi",
    )
    db.add(edition)
    db.flush()
    return edition


def _story_article(db, channel, suffix="a"):
    story = StoryCluster(
        channel_id=channel.id,
        canonical_title=f"Test story {suffix}",
        fingerprint=f"fingerprint-{suffix}",
        section="national",
        genre="news",
        risk_level="low",
    )
    db.add(story)
    db.flush()
    article = Article(story_id=story.id, title=f"Test article {suffix}", status="approved", risk_level="low")
    db.add(article)
    db.flush()
    return story, article


def test_progress_value_reports_stage_and_story_completion():
    assert _progress_value("queued", "") == 0
    assert _progress_value("layout", "Composing tabloid pages") == 84
    assert _progress_value("drafting", "Drafting story 5/10") == 53
    assert _progress_value("published", "Edition released") == 100


def test_schedule_cutoffs_are_timezone_aware_and_idempotent(db, demo_channel):
    schedule = _schedule(db, demo_channel)
    now = datetime(2026, 10, 22, 1, 0, tzinfo=UTC)  # 06:00 Asia/Karachi
    cutoffs = due_cutoffs(db, schedule, now=now)
    assert len(cutoffs) == 3
    assert cutoffs[-1] == datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
    assert all(cutoff < now for cutoff in cutoffs)

    created = enqueue_due_jobs(db, now=now)
    db.commit()
    assert len(created) == 3
    assert all(job.kind == "edition" and job.status == "scheduled" for job in created)
    payload = json.loads(created[-1].payload_json)
    assert payload["edition_kind"] == "daily"
    assert payload["requires_review"] is True
    assert payload["release_at"].startswith("2026-10-22T03:00:00")  # 08:00 PKT

    assert enqueue_due_jobs(db, now=now) == []
    assert db.query(Job).count() == 3

    next_day = now + timedelta(days=1)
    created = enqueue_due_jobs(db, now=next_day)
    db.commit()
    assert len(created) == 1
    assert json.loads(created[0].payload_json)["cutoff"].startswith("2026-10-23T00:00:00")


def test_manual_jobs_are_idempotent_and_claimable_with_a_lease(db, demo_channel):
    now = datetime(2026, 10, 22, tzinfo=UTC)
    first = enqueue_manual_job(
        db,
        demo_channel,
        window_start=now - timedelta(days=1),
        window_end=now,
        actor_id=1,
    )
    second = enqueue_manual_job(
        db,
        demo_channel,
        window_start=now - timedelta(days=1),
        window_end=now,
        actor_id=1,
    )
    db.commit()
    # The same channel and window must never queue two jobs or two editions.
    assert first.key == second.key
    assert first.id == second.id
    assert db.query(Job).count() == 1
    assert second.reused_existing is True

    claimed = claim_job(db, worker_id="worker-one", now=datetime.now(UTC))
    db.commit()
    assert claimed is not None and claimed.status == "running"
    assert claimed.attempt == 1
    assert claimed.lock_owner == "worker-one"

    # The only job is leased, so a second worker has nothing to claim.
    assert claim_job(db, worker_id="worker-two", now=datetime.now(UTC)) is None


def test_expired_worker_locks_are_recoverable(db, demo_channel):
    now = datetime.now(UTC)
    job = enqueue_manual_job(
        db,
        demo_channel,
        window_start=now - timedelta(days=1),
        window_end=now,
    )
    db.commit()
    job.status = "running"
    job.attempt = 1
    job.max_attempts = 3
    job.lock_owner = "crashed-worker"
    job.lock_expires_at = now - timedelta(minutes=5)
    db.commit()

    released = release_expired_locks(db, now=now)
    db.commit()
    assert released == 1
    assert job.status == "failed_retryable"
    assert job.lock_owner == ""
    assert job.lock_expires_at is None


def test_worker_file_lease_is_exclusive(tmp_path):
    path = tmp_path / "worker.lock"
    with WorkerLease(path):
        try:
            with WorkerLease(path):
                pass
        except WorkerBusy:
            pass
        else:  # pragma: no cover - only reached if the OS lock is not exclusive
            raise AssertionError("A second worker acquired the same lock")
    with WorkerLease(path):
        assert path.exists()
    with WorkerLease(path):
        pass
    assert path.read_text(encoding="utf-8").strip().isdigit()


def test_auto_publish_is_scoped_to_the_edition_and_requires_clean_pages(db, demo_channel):
    edition = _edition(db, demo_channel)
    story, article = _story_article(db, demo_channel, "a")
    other_story, other_article = _story_article(db, demo_channel, "b")
    db.add(EditionArticle(edition_id=edition.id, article_id=article.id, section="national", sort_order=0))
    db.commit()

    # No layout yet.
    assert "Layout has not been generated." in edition_review_blockers(db, edition.id)
    db.add(EditionPage(edition_id=edition.id, page_number=1, template="national"))
    db.commit()

    # A review flag on an unrelated story must not block this edition.
    db.add(
        ReviewFlag(
            article_id=other_article.id,
            story_id=other_story.id,
            category="unrelated",
            severity="block_publish",
            message="Flag belongs to another edition.",
            status="open",
        )
    )
    db.commit()
    assert edition_review_blockers(db, edition.id) == []
    assert finalize_auto_publish(db, edition)
    db.commit()
    assert edition.status == "published"
    assert edition.published_at is not None

    # A flag on this edition's own article does block it.
    blocked = _edition(db, demo_channel)
    db.add(EditionArticle(edition_id=blocked.id, article_id=article.id, section="national", sort_order=0))
    db.add(EditionPage(edition_id=blocked.id, page_number=1, template="national"))
    db.add(
        ReviewFlag(
            article_id=article.id,
            story_id=story.id,
            category="unsupported_claim",
            severity="block_publish",
            message="Needs a decision.",
            status="open",
        )
    )
    db.commit()
    assert edition_review_blockers(db, blocked.id)
    assert finalize_auto_publish(db, blocked) is False
    assert blocked.status == "awaiting_review"
