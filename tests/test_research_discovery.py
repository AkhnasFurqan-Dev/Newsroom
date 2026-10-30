"""Automatic discovery: queries, adapters, ranking and candidate policy.

These tests do not prove a live search provider was reachable. They define the
contracts the live adapters implement and isolate every network call behind an
injected transport, so the suite never depends on an external service.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from channel_press.models import ExternalReference, StoryCluster
from channel_press.research import (
    BraveSearchAdapter,
    GoogleCSEAdapter,
    SearchHit,
    SearchUnavailable,
    build_adapter,
    build_queries,
    discover_candidates,
    provider_status,
)
from channel_press.research.service import HttpResponse
from channel_press.config import settings

RESOLVER = lambda host: ["93.184.216.34"]  # noqa: E731


def _html(text: str, title: str = "Independent report") -> bytes:
    return (
        f"<html><head><title>{title}</title></head><body><p>{text}</p></body></html>"
    ).encode("utf-8")


class FakeTransport:
    def __init__(self, pages: dict[str, tuple[int, dict, bytes]]):
        self.pages = pages

    def __call__(self, url: str, *, timeout: float, max_bytes: int) -> HttpResponse:
        if url.endswith("/robots.txt"):
            return HttpResponse(
                200, {"content-type": "text/plain"}, b"User-agent: *\nAllow: /\n", url
            )
        status, headers, body = self.pages.get(
            url, (404, {"content-type": "text/html"}, b"<html><body>missing</body></html>")
        )
        return HttpResponse(status, headers, body, url)


class FakeAdapter:
    name = "fake"
    label = "Fake discovery"
    max_queries = 3

    def __init__(self, hits: list[SearchHit], *, ready: bool = True, reason: str = "ready"):
        self.hits = hits
        self.ready = ready
        self.reason = reason
        self.queries: list[str] = []

    def available(self) -> tuple[bool, str]:
        return self.ready, self.reason

    def search(self, query: str, *, count: int = 8) -> list[SearchHit]:
        self.queries.append(query)
        return self.hits[:count]


def _story(db, channel, title: str = "Quetta market blast", *, risk: str = "low") -> StoryCluster:
    story = StoryCluster(
        channel_id=channel.id,
        canonical_title=title,
        summary=title,
        section="national",
        genre="news",
        importance=0.8,
        risk_level=risk,
        fingerprint=f"discovery-{title}-{risk}",
    )
    db.add(story)
    db.flush()
    return story


def _profile(db):
    from channel_press.publications import default_publication

    return default_publication(db)


def _hits() -> list[SearchHit]:
    return [
        SearchHit(
            url="https://t.me/ThePakistanNews/23360",
            title="Quetta market blast channel post",
            publisher="t.me",
            published_at=datetime(2026, 10, 20, tzinfo=UTC),
        ),
        SearchHit(
            url="https://news.example/quetta-blast",
            title="Quetta market blast injures six",
            publisher="news.example",
            published_at=datetime(2026, 10, 20, 12, tzinfo=UTC),
            description="Independent reporting from Quetta on the market blast.",
        ),
        SearchHit(
            url="https://news.google.com/story/irrelevant",
            title="A market reopens elsewhere",
            publisher="news.google.com",
            published_at=datetime(2026, 10, 20, 12, tzinfo=UTC),
        ),
    ]


def test_provider_status_reports_configured_api_then_feed_fallback(monkeypatch):
    monkeypatch.setattr(settings, "google_cse_api_key", "")
    monkeypatch.setattr(settings, "google_cse_cx", "")
    monkeypatch.setattr(settings, "brave_search_api_key", "")
    state = provider_status("auto")
    assert state["status"] == "ready"
    assert state["provider"] == "rss"

    monkeypatch.setattr(settings, "google_cse_api_key", "key")
    monkeypatch.setattr(settings, "google_cse_cx", "cx")
    assert provider_status("auto")["provider"] == "google_cse"

    monkeypatch.setattr(settings, "google_cse_api_key", "")
    monkeypatch.setattr(settings, "google_cse_cx", "")
    assert provider_status("auto")["provider"] == "rss"

    missing = provider_status("brave")
    assert missing["status"] == "unavailable"
    assert "BRAVE" in missing["detail"].upper()


def test_build_queries_uses_only_anchors_stated_in_the_story():
    story = StoryCluster(
        id=1,
        canonical_title="Quetta market blast injures six",
        summary="A blast at a Quetta market injured six people on Monday.",
        section="national",
        genre="news",
        fingerprint="q",
    )
    queries = build_queries(
        story,
        ["A blast at a Quetta market injured six people on Monday, police said."],
        max_queries=5,
    )
    assert 1 <= len(queries) <= 5
    joined = " ".join(queries).lower()
    assert "quetta" in joined
    assert "blast" in joined
    assert "india" not in joined
    assert "karachi" not in joined
    assert len(set(query.lower() for query in queries)) == len(queries)


def test_google_adapter_parses_documented_payload():
    class Response:
        status_code = 200

        def json(self):
            return {
                "items": [
                    {
                        "link": "https://news.example/report",
                        "title": "Quetta blast report",
                        "displayLink": "news.example",
                        "snippet": "Independent report.",
                        "pagemap": {
                            "metatags": [
                                {"article:published_time": "2026-10-20T12:30:00Z"}
                            ]
                        },
                    }
                ]
            }

    def fake_get(*_args, **_kwargs):
        return Response()

    adapter = GoogleCSEAdapter(api_key="key", cx="cx", get=fake_get)
    hits = adapter.search("Quetta blast")
    assert len(hits) == 1
    assert hits[0].publisher == "news.example"
    assert hits[0].published_at == datetime(2026, 10, 20, 12, 30, tzinfo=UTC)


def test_brave_adapter_parses_documented_payload():
    class Response:
        status_code = 200

        def json(self):
            return {
                "web": {
                    "results": [
                        {
                            "url": "https://news.example/brave",
                            "title": "Independent account",
                            "description": "Context.",
                            "page_age": "2026-10-20T10:00:00Z",
                            "profile": {"long_name": "Example News"},
                        }
                    ]
                }
            }

    def fake_get(*_args, **_kwargs):
        return Response()

    adapter = BraveSearchAdapter(api_key="token", get=fake_get)
    hits = adapter.search("Quetta blast")
    assert hits[0].publisher == "Example News"
    assert hits[0].published_at == datetime(2026, 10, 20, 10, tzinfo=UTC)


def test_gdelt_adapter_parses_public_payload_and_parses_seendate():
    from channel_press.research import GdeltAdapter

    class Response:
        status_code = 200

        def json(self):
            return {
                "articles": [
                    {
                        "url": "https://news.example/gdelt-report",
                        "title": "Quetta market blast report",
                        "domain": "news.example",
                        "seendate": "20261020T123000Z",
                    }
                ]
            }

    def fake_get(*_args, **_kwargs):
        return Response()

    adapter = GdeltAdapter(get=fake_get)
    hits = adapter.search("Quetta blast")
    assert hits[0].publisher == "news.example"
    assert hits[0].published_at == datetime(2026, 10, 20, 12, 30, tzinfo=UTC)


def test_rss_adapter_filters_unrelated_feed_items():
    from channel_press.research import RssAdapter

    feed = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Quetta market blast injures six</title><link>https://news.example/quetta</link>
      <description>Independent report.</description><pubDate>Tue, 20 Oct 2026 12:00:00 +0000</pubDate></item>
      <item><title>Indians turn to start-ups for care</title><link>https://news.example/unrelated</link></item>
    </channel></rss>"""

    class Response:
        status_code = 200
        content = feed

    adapter = RssAdapter(get=lambda *_args, **_kwargs: Response())
    hits = adapter.search("Quetta market blast", count=8)
    assert [hit.url for hit in hits] == ["https://news.example/quetta"]
    assert hits[0].published_at == datetime(2026, 10, 20, 12, tzinfo=UTC)


def test_query_builder_ignores_channel_admin_labels_and_invents_nothing():
    story = StoryCluster(
        id=2,
        canonical_title="NOTE: channel posting schedule changes",
        summary="We will reduce postings.",
        section="national",
        genre="news",
        fingerprint="admin",
    )
    queries = build_queries(story, ["NOTE: We will reduce postings. Telegram search is blocked."])
    assert all("note" not in query.lower() for query in queries)
    assert all("india" not in query.lower() for query in queries)


def test_google_adapter_without_credentials_is_explicitly_unavailable():
    adapter = GoogleCSEAdapter(api_key="", cx="")
    with pytest.raises(SearchUnavailable):
        adapter.search("anything")


def test_discovery_skips_social_hosts_fetches_and_keeps_candidates(
    db, demo_channel, owner
):
    profile = _profile(db)
    story = _story(db, demo_channel)
    adapter = FakeAdapter(_hits())
    transport = FakeTransport(
        {
            "https://news.example/quetta-blast": (
                200,
                {"content-type": "text/html"},
                _html("Independent reporting from Quetta on the market blast. " * 6),
            )
        }
    )
    result = discover_candidates(
        db,
        story,
        profile,
        adapter=adapter,
        transport=transport,
        resolver=RESOLVER,
        window_start=datetime(2026, 10, 18, tzinfo=UTC),
        window_end=datetime(2026, 10, 22, tzinfo=UTC),
    )
    assert result.status == "ok"
    assert result.provider == "fake"
    assert result.queries
    assert adapter.queries
    stored = list(db.query(ExternalReference).filter_by(story_id=story.id))
    assert len(stored) == 1, [(row.url, row.review_status) for row in stored]
    reference = stored[0]
    assert reference.provider == "search:fake"
    assert reference.review_status == "candidate"
    assert reference.reachable is True
    assert reference.http_status == 200
    assert reference.excerpt
    assert reference.search_query
    assert reference.relevance >= 0
    assert reference.publisher == "news.example"

    again = discover_candidates(
        db,
        story,
        profile,
        adapter=adapter,
        transport=transport,
        resolver=RESOLVER,
        window_start=datetime(2026, 10, 18, tzinfo=UTC),
        window_end=datetime(2026, 10, 22, tzinfo=UTC),
    )
    assert again.created == []
    assert again.skipped >= 1


def test_unavailable_provider_is_reported_without_raising(db, demo_channel):
    profile = _profile(db)
    story = _story(db, demo_channel, "Unavailable provider story")
    result = discover_candidates(
        db,
        story,
        profile,
        adapter=FakeAdapter([], ready=False, reason="No credential configured."),
    )
    assert result.status == "unavailable"
    assert "credential" in result.detail
    assert result.created == []


def test_auto_context_accepts_only_ordinary_dated_pages(db, demo_channel):
    profile = _profile(db)
    profile.research_auto_accept = "auto_context"
    transport = FakeTransport(
        {
            "https://news.example/quetta-blast": (
                200,
                {"content-type": "text/html"},
                _html("Independent reporting from Quetta on the market blast. " * 6),
            )
        }
    )
    ordinary = _story(db, demo_channel, "Quetta market blast injures six")
    result = discover_candidates(
        db,
        ordinary,
        profile,
        adapter=FakeAdapter(_hits()),
        transport=transport,
        resolver=RESOLVER,
        window_start=datetime(2026, 10, 18, tzinfo=UTC),
        window_end=datetime(2026, 10, 22, tzinfo=UTC),
    )
    assert result.created
    assert result.created[0].review_status == "accepted"

    # A casualty/allegation story always waits for a human decision.
    high_risk = _story(
        db, demo_channel, "Quetta market blast kills six, panel alleges corruption", risk="high"
    )
    result = discover_candidates(
        db,
        high_risk,
        profile,
        adapter=FakeAdapter(_hits()),
        transport=transport,
        resolver=RESOLVER,
        window_start=datetime(2026, 10, 18, tzinfo=UTC),
        window_end=datetime(2026, 10, 22, tzinfo=UTC),
    )
    assert result.created
    assert result.created[0].review_status == "candidate"


def test_discovery_off_policy_does_not_search(db, demo_channel):
    profile = _profile(db)
    profile.research_policy = "off"
    story = _story(db, demo_channel, "Disabled research story")
    adapter = FakeAdapter(_hits())
    result = discover_candidates(db, story, profile, adapter=adapter)
    assert result.status == "disabled"
    assert adapter.queries == []
    assert result.created == []
