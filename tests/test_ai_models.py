from __future__ import annotations

import importlib
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from channel_press.ai import model_manager, providers
from channel_press.ai.model_profiles import MODEL_MANIFEST, MODEL_PROFILES
from channel_press.ai.providers import (
    DraftResult,
    OllamaDraftProvider,
    SourceEvidence,
    StoryBundle,
)
from channel_press.config import settings
from channel_press.editorial.clustering import build_stories
from channel_press.editorial.drafting import draft_story
from channel_press.models import (
    AppSetting,
    Channel,
    ModelRun,
    PostFragment,
    SourcePost,
    StoryCluster,
)
from channel_press.pipeline import PipelineError, select_provider

WINDOW = (
    datetime(2026, 10, 18, tzinfo=UTC),
    datetime(2026, 10, 22, tzinfo=UTC),
)


def test_model_manifest_pins_three_profiles_and_apache_license():
    assert MODEL_MANIFEST["upstream"]["license"] == "Apache-2.0"
    assert MODEL_MANIFEST["upstream"]["license_layer_verified"] is True
    assert MODEL_PROFILES["standard"]["tag"] == "qwen3.5:4b-q4_K_M"
    assert MODEL_PROFILES["enhanced"]["tag"] == "qwen3.5:9b-q4_K_M"
    assert MODEL_PROFILES["compact"]["tag"] == "qwen3.5:2b"
    assert len(MODEL_PROFILES["standard"]["registry_manifest_digest"]) == 71
    assert len(MODEL_PROFILES["enhanced"]["registry_manifest_digest"]) == 71


def test_installed_profile_uses_pinned_digest_not_registry_layer_size(monkeypatch):
    profile = MODEL_PROFILES["compact"]
    installed = {
        "name": profile["tag"],
        "size": profile["download_bytes"] + 175,
        "digest": profile["registry_manifest_digest"].removeprefix("sha256:"),
    }
    monkeypatch.setattr(model_manager, "installed_models", lambda: [installed])

    assert model_manager.installed_profile(profile) == installed

    monkeypatch.setattr(model_manager, "installed_models", lambda: [{**installed, "digest": "wrong"}])
    assert model_manager.installed_profile(profile) is None


def test_ai_models_page_lists_pinned_profiles_inside_authenticated_app(client, auth, monkeypatch):
    web_module = importlib.import_module("channel_press.web.app")
    monkeypatch.setattr(web_module, "installed_models", list)

    response = client.get("/ai-models")

    assert response.status_code == 200
    for model_tag in ("qwen3.5:4b-q4_K_M", "qwen3.5:9b-q4_K_M", "qwen3.5:2b"):
        assert model_tag in response.text
    assert "Apache-2.0" in response.text
    assert "Test local model" not in response.text


def test_qwen35_request_disables_thinking_and_uses_a_json_schema(monkeypatch):
    sent = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "title": "Fictional ward reports a route change",
                            "deck": "",
                            "standfirst": "",
                            "body": [
                                "The fictional Example Ward fixture recorded exactly three sample buses changing routes on 30 October 2026."
                            ],
                        }
                    ),
                    "thinking": "This field must not become article copy.",
                }
            }

    class Client:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def post(self, _url, *, json):
            sent.update(json)
            return Response()

    monkeypatch.setattr(providers.httpx, "Client", Client)
    provider = OllamaDraftProvider(model=MODEL_PROFILES["standard"]["tag"])
    bundle = StoryBundle(
        story=SimpleNamespace(
            canonical_title="Synthetic source test",
            genre="news",
        ),
        sources=[
            SourceEvidence(
                post_id=1,
                message_id=1,
                posted_at="2026-10-30T00:00:00+00:00",
                text=(
                    "Synthetic fixture FX-01: the fictional Example Ward recorded exactly "
                    "three sample buses changing routes on 30 October 2026."
                ),
                source_url="",
                source_label="Synthetic fixture FX-01",
            )
        ],
        claims=[],
        timeline=[],
    )

    result = provider.draft(bundle)

    assert result.provider == "ollama"
    assert result.model == "qwen3.5:4b-q4_K_M"
    assert result.body and "thinking" not in " ".join(result.body).lower()
    assert sent["think"] is False
    assert isinstance(sent["format"], dict)
    assert sent["format"]["properties"]["body"]["type"] == "array"
    assert result.generation_settings["num_ctx"] == provider.num_ctx


def test_provider_requires_exact_pinned_tag_and_digest(monkeypatch):
    profile = MODEL_PROFILES["standard"]
    installed = {
        "name": profile["tag"],
        "size": profile["download_bytes"] + 175,
        "digest": "not-the-pinned-digest",
    }

    class Response:
        status_code = 200

        def __init__(self, url):
            self.url = url

        def json(self):
            if self.url.endswith("/api/tags"):
                return {"models": [installed]}
            return {"version": "test"}

    class Client:
        def __init__(self, **_):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def get(self, url):
            return Response(url)

    monkeypatch.setattr(providers.httpx, "Client", Client)
    provider = OllamaDraftProvider(model=profile["tag"])
    available, message = provider.available()
    assert available is False
    assert "AI Models" in message

    installed["digest"] = profile["registry_manifest_digest"].removeprefix("sha256:")
    available, message = provider.available()
    assert available is True
    assert message == "ready"
    assert provider.model_digest == installed["digest"]


def test_real_source_provider_requires_smoke_test_for_exact_local_digest(db, monkeypatch):
    monkeypatch.setattr(settings, "ollama_model", MODEL_PROFILES["standard"]["tag"])
    channel = Channel(slug="authorized-live", title="Authorized live source")
    db.add(channel)
    db.flush()

    def available(provider):
        provider.model_digest = "local-digest"
        return True, "ready"

    monkeypatch.setattr(OllamaDraftProvider, "available", available)
    monkeypatch.setattr(
        "channel_press.ai.model_manager.model_smoke_record",
        lambda _db, _model, _digest: None,
    )
    with pytest.raises(PipelineError, match="not tested for this exact digest"):
        select_provider(channel, db=db)

    monkeypatch.setattr(
        "channel_press.ai.model_manager.model_smoke_record",
        lambda _db, _model, digest: {"status": "passed"} if digest == "local-digest" else None,
    )
    provider, description = select_provider(channel, db=db)
    assert provider.model == MODEL_PROFILES["standard"]["tag"]
    assert description == f"ollama {provider.model}"


def test_model_runs_record_new_provenance_without_rewriting_history(
    db, demo_channel, sample_import, client, auth, monkeypatch
):
    build_stories(
        db,
        demo_channel.id,
        *WINDOW,
    )
    post = db.query(SourcePost).filter_by(telegram_message_id=101).one()
    fragment = db.query(PostFragment).filter_by(post_id=post.id).first()
    assert fragment is not None and fragment.story_id is not None
    story = db.get(StoryCluster, fragment.story_id)

    class Qwen35FixtureProvider:
        name = "ollama"

        @staticmethod
        def draft(bundle):
            return DraftResult(
                title="The source records a developing road closure",
                deck="",
                standfirst="",
                body=[bundle.sources[0].text],
                provider="ollama",
                model="qwen3.5:4b-q4_K_M",
                prompt_version="news-article-v3",
                latency_ms=42,
                model_digest="local-model-digest",
                provider_version="0.40.10",
                generation_settings={"think": False, "num_ctx": 4096},
            )

    article = draft_story(db, story, Qwen35FixtureProvider())
    historical = ModelRun(
        task="news_article",
        provider="ollama",
        model="qwen2.5:3b-instruct",
        prompt_version="news-article-v2",
        input_hash="a" * 64,
        output_hash="b" * 64,
        valid=True,
        latency_ms=900,
    )
    db.add(historical)
    db.commit()
    historical_id = historical.id

    profile = MODEL_PROFILES["standard"]
    web_module = importlib.import_module("channel_press.web.app")
    monkeypatch.setattr(
        web_module,
        "installed_profile",
        lambda selected: {
            "name": selected["tag"],
            "digest": "local-model-digest",
            "size": selected["download_bytes"],
        },
    )
    response = client.post(
        "/ai-models/select",
        data={"profile_id": "standard", "csrf_token": auth},
        follow_redirects=False,
    )
    assert response.status_code == 303

    saved = db.query(ModelRun).filter_by(article_id=article.id).one()
    assert saved.model == profile["tag"]
    assert saved.model_digest == "local-model-digest"
    assert saved.provider_version == "0.40.10"
    assert json.loads(saved.generation_settings_json) == {
        "think": False,
        "num_ctx": 4096,
    }
    evidence = json.loads(saved.evidence_manifest_json)
    assert post.id in evidence["source_post_ids"]
    assert db.get(ModelRun, historical_id).model == "qwen2.5:3b-instruct"
    assert db.get(AppSetting, "ai.model_tag").value == profile["tag"]


@pytest.mark.live
def test_live_qwen35_standard_runs_through_editorial_audit(
    db, demo_channel, sample_import
):
    if os.environ.get("CHANNEL_PRESS_RUN_LIVE_MODEL_TESTS") != "1":
        pytest.skip("Set CHANNEL_PRESS_RUN_LIVE_MODEL_TESTS=1 to run local inference.")

    profile = MODEL_PROFILES["standard"]
    provider = OllamaDraftProvider(model=profile["tag"])
    available, message = provider.available()
    assert available, message

    build_stories(db, demo_channel.id, *WINDOW)
    post = db.query(SourcePost).filter_by(telegram_message_id=101).one()
    fragment = db.query(PostFragment).filter_by(post_id=post.id).first()
    assert fragment is not None and fragment.story_id is not None
    story = db.get(StoryCluster, fragment.story_id)
    assert story is not None

    article = draft_story(db, story, provider)
    run = db.query(ModelRun).filter_by(article_id=article.id).one()
    audit = json.loads(article.audit_json)

    assert run.provider == "ollama"
    assert run.model == profile["tag"]
    assert run.model_digest == profile["registry_manifest_digest"].removeprefix("sha256:")
    assert run.status == "completed"
    assert audit["provider"] == "ollama"
    assert 0 <= audit["citation_coverage"] <= 1
