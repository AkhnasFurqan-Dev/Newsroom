"""Bounded research fetching, review state and print eligibility."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import importlib

from fastapi.testclient import TestClient

from channel_press.layout.planner import create_plan
from channel_press.models import (
    Article,
    Edition,
    EditionArticle,
    ExternalReference,
    StoryCluster,
    User,
)
from channel_press.publications import default_publication
from channel_press.research import (
    ResearchError,
    accept_reference,
    fetch_document,
    reject_reference,
    research_reference,
)
from channel_press.research.service import HttpResponse, normalise_url
from channel_press.security import hash_password
from tests.conftest import OWNER_PASSWORD, login

RESOLVER = lambda host: ["93.184.216.34"]  # noqa: E731
PAGE = "https://news.example/report"


def _html(text: str, title: str = "Independent report") -> bytes:
    return (
        f"<html><head><title>{title}</title><style>.x{{color:red}}</style></head>"
        f"<body><script>steal()</script><p>{text}</p></body></html>"
    ).encode("utf-8")


class FakeTransport:
    def __init__(self, pages: dict[str, HttpResponse | tuple[int, dict, bytes]], robots: str = "User-agent: *\nAllow: /\n"):
        self.pages = pages
        self.robots = robots

    def __call__(self, url: str, *, timeout: float, max_bytes: int) -> HttpResponse:
        if url.endswith("/robots.txt"):
            return HttpResponse(200, {"content-type": "text/plain"}, self.robots.encode("utf-8"), url)
        item = self.pages.get(url)
        if item is None:
            return HttpResponse(404, {"content-type": "text/html"}, b"<html><body>missing</body></html>", url)
        if isinstance(item, HttpResponse):
            return item
        status, headers, body = item
        return HttpResponse(status, headers, body, url)


def _story(db, channel, title: str = "Quetta market blast") -> StoryCluster:
    story = StoryCluster(
        channel_id=channel.id,
        canonical_title=title,
        summary=title,
        section="national",
        genre="news",
        importance=0.6,
        risk_level="low",
        fingerprint=f"fingerprint-{title}",
    )
    db.add(story)
    db.flush()
    return story


def test_url_validation_rejects_non_http_credentials_and_local_hosts():
    assert normalise_url("https://news.example/a#fragment") == "https://news.example/a"
    for bad in (
        "file:///C:/secret.txt",
        "javascript:alert(1)",
        "https://user:pass@news.example/a",
        "http://127.0.0.1/admin",
        "http://localhost/admin",
        "http://[::1]/admin",
    ):
        with pytest.raises(ResearchError):
            normalise_url(bad)


def test_private_and_reserved_addresses_are_never_fetched():
    transport = FakeTransport({PAGE: (200, {"content-type": "text/html"}, _html("x" * 200))})
    for host, resolved in (
        ("internal.example", ["10.1.2.3"]),
        ("loop.example", ["127.0.0.1"]),
        ("link.example", ["169.254.169.254"]),
        ("documentation.example", ["192.0.2.10"]),
    ):
        with pytest.raises(ResearchError):
            fetch_document(
                f"https://{host}/page",
                transport=transport,
                resolver=lambda _host, values=resolved: values,
            )


def test_private_redirect_target_is_rejected():
    start = "https://news.example/start"
    transport = FakeTransport(
        {
            start: (302, {"location": "http://10.0.0.5/secret", "content-type": "text/html"}, b""),
        }
    )
    with pytest.raises(ResearchError):
        fetch_document(start, transport=transport, resolver=RESOLVER)


def test_robots_disallow_and_bad_content_type_are_rejected():
    transport = FakeTransport(
        {PAGE: (200, {"content-type": "text/html"}, _html("A public report. " * 20))},
        robots="User-agent: *\nDisallow: /\n",
    )
    with pytest.raises(ResearchError) as denied:
        fetch_document(PAGE, transport=transport, resolver=RESOLVER)
    assert "robots" in str(denied.value).lower()

    transport = FakeTransport(
        {PAGE: (200, {"content-type": "application/pdf"}, b"%PDF-1.7" + b"x" * 200)}
    )
    with pytest.raises(ResearchError):
        fetch_document(PAGE, transport=transport, resolver=RESOLVER)


def test_oversize_page_is_rejected():
    transport = FakeTransport(
        {PAGE: (200, {"content-type": "text/html"}, _html("A public report. " * 200))}
    )
    with pytest.raises(ResearchError):
        fetch_document(PAGE, transport=transport, resolver=RESOLVER, max_bytes=500)


def test_successful_fetch_extracts_title_and_text_without_scripts():
    transport = FakeTransport(
        {
            PAGE: (
                200,
                {"content-type": "text/html; charset=utf-8"},
                _html("Independent reporting on the blast. " * 10, "Quetta blast report"),
            )
        }
    )
    outcome = fetch_document(PAGE, transport=transport, resolver=RESOLVER)
    assert outcome.title == "Quetta blast report"
    assert "Independent reporting" in outcome.text
    assert "steal" not in outcome.text
    assert len(outcome.content_sha256) == 64
    assert "robots.txt" in outcome.terms_note


def test_candidate_is_stored_deduplicated_and_capped(db, demo_channel):
    story = _story(db, demo_channel)
    second = "https://news.example/report-two"
    transport = FakeTransport(
        {
            PAGE: (200, {"content-type": "text/html"}, _html("Independent reporting on the blast. " * 10)),
            second: (200, {"content-type": "text/html"}, _html("A second report with different wording. " * 10)),
        }
    )
    reference, created = research_reference(
        db, story, PAGE, transport=transport, resolver=RESOLVER
    )
    assert created is True
    assert reference.provider == "research:v1"
    assert reference.review_status == "candidate"
    assert reference.http_status == 200
    assert reference.terms_note
    assert reference.reachable is True

    again, created_again = research_reference(
        db, story, PAGE, transport=transport, resolver=RESOLVER
    )
    assert again.id == reference.id
    assert created_again is False

    with pytest.raises(ResearchError):
        research_reference(
            db, story, second, transport=transport, resolver=RESOLVER, max_per_story=1
        )


def test_candidate_stays_out_of_the_plan_until_accepted(db, demo_channel, owner):
    ensure_profile = default_publication(db)
    assert ensure_profile is not None
    story = _story(db, demo_channel)
    article = Article(
        story_id=story.id,
        title="Quetta market blast kills two",
        deck="",
        standfirst="",
        body_json=json.dumps(
            [
                "Security forces said two people were killed in a blast at a Quetta market on Monday.",
                "The district administration said the wounded were taken to hospital.",
            ]
        ),
        body_text=(
            "Security forces said two people were killed in a blast at a Quetta market on Monday.\n\n"
            "The district administration said the wounded were taken to hospital."
        ),
        genre="news",
        section="national",
        status="approved",
        word_count=30,
        revision=1,
    )
    db.add(article)
    db.flush()
    edition = Edition(
        channel_id=demo_channel.id,
        publication_id=ensure_profile.id,
        kind="manual",
        title="Research proof",
        volume=1,
        issue_number=1,
        status="draft",
        window_start=datetime(2026, 10, 18, tzinfo=UTC),
        window_end=datetime(2026, 10, 22, tzinfo=UTC),
        cutoff_at=datetime(2026, 10, 21, tzinfo=UTC),
        target_release_at=datetime(2026, 10, 22, tzinfo=UTC),
        timezone="Asia/Karachi",
    )
    db.add(edition)
    db.flush()
    db.add(
        EditionArticle(
            edition_id=edition.id,
            article_id=article.id,
            section="national",
            sort_order=0,
            is_front=True,
        )
    )
    reference = ExternalReference(
        story_id=story.id,
        url="https://news.example/report",
        publisher="news.example",
        title="Independent report",
        excerpt="Background context.",
        provider="research:v1",
        reachable=True,
        review_status="candidate",
        http_status=200,
        content_sha256="a" * 64,
        terms_note="robots.txt permits this page; editorial context only.",
    )
    db.add(reference)
    db.flush()

    plan = create_plan(db, edition, ensure_profile, media_dir=None, download_media=False)
    assert reference.url not in [entry.get("url") for entry in plan.appendix_entries]

    accept_reference(db, reference)
    db.flush()
    plan = create_plan(db, edition, ensure_profile, media_dir=None, download_media=False)
    assert reference.url in [entry.get("url") for entry in plan.appendix_entries]

    reject_reference(db, reference)
    db.flush()
    plan = create_plan(db, edition, ensure_profile, media_dir=None, download_media=False)
    assert reference.url not in [entry.get("url") for entry in plan.appendix_entries]


def test_research_route_requires_csrf_and_records_provenance(
    client, db, demo_channel, owner, auth, monkeypatch
):
    story = _story(db, demo_channel, "Route research story")
    db.commit()

    def fake_research(db_session, story_row, url, **kwargs):
        reference = ExternalReference(
            story_id=story_row.id,
            url="https://news.example/route",
            publisher="news.example",
            title="Route report",
            excerpt="Background.",
            provider="research:v1",
            reachable=True,
            review_status="candidate",
            http_status=200,
            terms_note="robots.txt permits this page.",
        )
        db_session.add(reference)
        db_session.flush()
        return reference, True

    monkeypatch.setattr(
        importlib.import_module("channel_press.web.app"), "research_reference", fake_research
    )

    missing = client.post(
        f"/stories/{story.id}/research",
        data={"url": "https://news.example/route"},
        follow_redirects=False,
    )
    assert missing.status_code == 403

    response = client.post(
        f"/stories/{story.id}/research",
        data={"url": "https://news.example/route", "csrf_token": auth},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db.expire_all()
    stored = db.query(ExternalReference).filter_by(story_id=story.id).one()
    assert stored.review_status == "candidate"
    assert stored.provider == "research:v1"


def test_accept_and_reject_routes_enforce_editor_role(client, db, demo_channel, owner, auth):
    story = _story(db, demo_channel, "Review route story")
    reference = ExternalReference(
        story_id=story.id,
        url="https://news.example/review",
        publisher="news.example",
        title="Review report",
        provider="research:v1",
        review_status="candidate",
    )
    db.add(reference)
    db.flush()
    db.commit()

    accepted = client.post(
        f"/stories/{story.id}/research/{reference.id}/review",
        data={"decision": "accept", "csrf_token": auth},
        follow_redirects=False,
    )
    assert accepted.status_code == 303
    db.expire_all()
    assert db.get(ExternalReference, reference.id).review_status == "accepted"

    rejected = client.post(
        f"/stories/{story.id}/research/{reference.id}/review",
        data={"decision": "reject", "csrf_token": auth},
        follow_redirects=False,
    )
    assert rejected.status_code == 303
    db.expire_all()
    assert db.get(ExternalReference, reference.id).review_status == "rejected"

    viewer = User(
        username="research-viewer",
        password_hash=hash_password(OWNER_PASSWORD),
        display_name="Research Viewer",
        role="viewer",
    )
    db.add(viewer)
    db.commit()
    viewer_client = TestClient(client.app, base_url="http://testserver")
    try:
        viewer_csrf = login(viewer_client, "research-viewer")
        forbidden = viewer_client.post(
            f"/stories/{story.id}/research/{reference.id}/review",
            data={"decision": "accept", "csrf_token": viewer_csrf},
            follow_redirects=False,
        )
        assert forbidden.status_code == 403
    finally:
        viewer_client.close()
