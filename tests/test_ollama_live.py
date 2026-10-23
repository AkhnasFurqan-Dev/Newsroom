"""Live local-model checks.

These run against a real Ollama server and are skipped automatically when the
server or the configured model is missing, so the offline suite stays
deterministic. Run them explicitly with:

    python -m pytest tests/test_ollama_live.py -m live
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from channel_press.ai.providers import OllamaDraftProvider, gather_bundle
from channel_press.editorial.claims import build_claims
from channel_press.editorial.clustering import build_stories
from channel_press.models import PostFragment, SourcePost, StoryCluster
from channel_press.pipeline import PipelineRequest, run_pipeline
from channel_press.timeutil import utcnow
from tests.conftest import import_fixture

pytestmark = pytest.mark.live

WINDOW = (
    datetime(2026, 10, 18, tzinfo=UTC),
    datetime(2026, 10, 22, tzinfo=UTC),
)


def _first_story(db) -> StoryCluster:
    post = db.query(SourcePost).filter_by(telegram_message_id=101).one()
    fragment = db.query(PostFragment).filter_by(post_id=post.id).first()
    assert fragment is not None and fragment.story_id is not None
    return db.get(StoryCluster, fragment.story_id)


def test_local_model_is_available_and_drafts_from_evidence(db, demo_channel, sample_import):
    provider = OllamaDraftProvider()
    ok, reason = provider.available()
    if not ok:
        pytest.skip(reason)

    build_stories(db, demo_channel.id, *WINDOW)
    story = _first_story(db)
    bundle = gather_bundle(db, story)
    bundle.claims = build_claims(db, story)
    assert bundle.sources

    result = provider.draft(bundle)
    provider.unload()

    assert result.provider == "ollama"
    assert result.model == provider.model
    assert result.title.strip()
    assert result.body
    assert len(" ".join(result.body).split()) >= 40
    assert result.latency_ms > 0


@pytest.mark.slow
def test_real_local_model_builds_a_thirty_post_edition(db, demo_channel, owner, tmp_path):
    provider = OllamaDraftProvider()
    ok, reason = provider.available()
    if not ok:
        pytest.skip(reason)

    import_fixture(db, demo_channel, owner, "posts_30.json")
    result = run_pipeline(
        db,
        PipelineRequest(
            channel=demo_channel,
            window_start=WINDOW[0],
            window_end=WINDOW[1],
            cutoff_at=datetime(2026, 10, 21, tzinfo=UTC),
            target_release_at=utcnow(),
            kind="manual",
            title="Local model proof edition",
            actor_id=owner.id,
            provider=provider,
            publish=True,
            mark_published=False,
            demo_edition=True,
            output_root=tmp_path,
        ),
    )
    db.commit()

    assert result.provider == "ollama"
    assert result.stats["stories"] >= 5
    assert result.publish_result is not None
    manifest = json.loads(result.edition.manifest_json or "{}")
    assert manifest["pdf"]["page_count"] >= 2
    assert manifest["quality"]["warnings"] == []

    fallbacks = {
        article.id: json.loads(article.audit_json or "{}").get("fallback_reason")
        for article in result.articles
        if json.loads(article.audit_json or "{}").get("fallback_reason")
    }
    assert not fallbacks, f"stories fell back to the fixture composer: {fallbacks}"

    print(
        f"LOCAL-MODEL 30 posts: {result.stats['stories']} stories, "
        f"{result.stats['articles']} articles, {manifest['pdf']['page_count']} pages, "
        f"{result.duration_seconds}s, provider={result.provider}"
    )
