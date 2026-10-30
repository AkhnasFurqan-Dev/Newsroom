"""Production-refinement UI: health view, edition naming and research controls."""

from __future__ import annotations

from datetime import UTC, datetime

from channel_press.models import Edition
from channel_press.preflight import Check, PreflightReport
from channel_press.publications import default_publication


def _edition(db, channel, *, status: str = "draft") -> Edition:
    profile = default_publication(db)
    edition = Edition(
        channel_id=channel.id,
        publication_id=profile.id,
        kind="manual",
        title="The Pakistan News — Manual",
        display_name="",
        volume=1,
        issue_number=7,
        status=status,
        window_start=datetime(2026, 10, 28, tzinfo=UTC),
        window_end=datetime(2026, 10, 29, tzinfo=UTC),
        cutoff_at=datetime(2026, 10, 29, tzinfo=UTC),
        target_release_at=datetime(2026, 10, 29, 8, tzinfo=UTC),
        timezone="Asia/Karachi",
    )
    db.add(edition)
    db.commit()
    return edition


def test_system_check_returns_a_readable_view_and_keeps_json_separate(
    client, owner, auth, monkeypatch
):
    import importlib

    web_app = importlib.import_module("channel_press.web.app")
    report = PreflightReport(
        checks=[Check("database", "Database", "ok", "Test database readable.")],
        telegram={},
        ollama={"model_present": True, "reachable": True, "detail": "Model installed."},
    )
    monkeypatch.setattr(web_app, "build_preflight", lambda **_kwargs: report)
    monkeypatch.setattr(
        web_app.telegram_service,
        "preflight",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            web_app.TelegramTransportError("No live session in this test.")
        ),
    )

    response = client.post("/system/check", data={"csrf_token": auth})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    for label in ("Application", "Database", "Telegram account", "Local AI", "Edition worker"):
        assert label in body
    assert '"pid"' not in body
    assert "{" not in body.split("<style")[0]

    legacy = client.post("/system/check.json", data={"csrf_token": auth})
    assert legacy.status_code == 200
    assert legacy.json()["web"] == "Connected and tested"


def test_build_form_uses_an_edition_name_not_a_workflow_kind(
    client, owner, auth, demo_channel
):
    response = client.get("/build")
    assert response.status_code == 200
    assert "Edition name" in response.text
    assert "Manual edition" not in response.text
    assert 'name="kind" value="manual"' in response.text


def test_edition_rename_updates_draft_but_not_published_versions(client, db, owner, auth, demo_channel):
    edition = _edition(db, demo_channel)
    response = client.post(
        f"/editions/{edition.id}/rename",
        data={"display_name": "Daily Edition", "csrf_token": auth},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.expire_all()
    assert db.get(Edition, edition.id).display_name == "Daily Edition"

    frozen = _edition(db, demo_channel, status="published")
    response = client.post(
        f"/editions/{frozen.id}/rename",
        data={"display_name": "Late rewrite", "csrf_token": auth},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.expire_all()
    assert db.get(Edition, frozen.id).display_name == ""


def test_branding_saves_research_and_print_policy(client, db, owner, auth):
    response = client.post(
        "/branding",
        data={
            "csrf_token": auth,
            "name": "The Pakistan News",
            "channel_handle": "@ThePakistanNews",
            "byline": "Editorial Desk",
            "source_disclosure": "Internal note.",
            "slogan": "",
            "city": "Karachi",
            "timezone": "Asia/Karachi",
            "accent": "#7b1f24",
            "paper_size": "tabloid",
            "contact_url": "",
            "map_profile": "neutral",
            "qr_strategy": "story",
            "show_chronology": "auto",
            "research_policy": "automatic_substantial",
            "research_provider": "rss",
            "research_queries_per_story": "2",
            "research_max_per_story": "4",
            "research_auto_accept": "review",
            "issue_volume": "1",
            "section_order": ["national", "international"],
            "allow_closing_light": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.expire_all()
    profile = default_publication(db)
    assert profile is not None
    assert profile.research_policy == "automatic_substantial"
    assert profile.research_provider == "rss"
    assert profile.research_queries_per_story == 2
    assert profile.research_max_per_story == 4
    assert profile.qr_strategy == "story"
    assert profile.show_slogan is False


def test_branding_rejects_an_unknown_research_provider(client, owner, auth):
    response = client.post(
        "/branding",
        data={
            "csrf_token": auth,
            "name": "The Pakistan News",
            "timezone": "Asia/Karachi",
            "accent": "#7b1f24",
            "paper_size": "tabloid",
            "map_profile": "neutral",
            "qr_strategy": "story",
            "show_chronology": "auto",
            "research_policy": "editor_triggered",
            "research_provider": "scrape-google",
            "research_auto_accept": "review",
            "issue_volume": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "err=" in response.headers["location"]
