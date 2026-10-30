"""Pluggable, terms-respecting story discovery for the research workflow.

This module answers a different question from :mod:`service`: not "fetch this
URL the editor typed" but "which independent public reports might corroborate
or contextualise this story?". It never scrapes a search-engine results page.
Each adapter talks to a documented programmatic API:

* Google Programmable Search (Custom Search JSON API), when an API key and
  search-engine id are configured;
* Brave Search API, when a subscription token is configured;
* GDELT DOC 2.0, a keyless public research API used as the honest fallback.

The adapters only discover candidate URLs and metadata. Fetching the page still
goes through :func:`channel_press.research.service.fetch_document`, so the same
SSRF, robots.txt, redirect, size and timeout protections apply. A discovered
page remains a *candidate* until an editor accepts it.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Callable, Protocol
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ExternalReference, PublicationProfile, StoryCluster
from ..timeutil import as_utc, utcnow
from .service import (
    ResearchError,
    Transport,
    fetch_document,
    normalise_url,
)

SEARCH_PROVIDER_PREFIX = "search:"
SOCIAL_HOSTS = {
    "t.me", "telegram.me", "x.com", "twitter.com", "facebook.com", "fb.com",
    "instagram.com", "youtube.com", "youtu.be", "tiktok.com", "reddit.com",
    "linkedin.com", "threads.net", "whatsapp.com", "pinterest.com",
}
AGGREGATOR_HOSTS = {
    "news.google.com", "flipboard.com", "msn.com", "yahoo.com", "feedspot.com",
}
_EVENT_WORDS = {
    "blast", "explosion", "bomb", "attack", "strike", "raid", "clash", "fire",
    "flood", "earthquake", "storm", "killed", "injured", "wounded", "arrested",
    "detained", "sentenced", "protest", "demonstration", "strike", "ban",
    "restriction", "agreement", "deal", "meeting", "visit", "summit", "election",
    "appointed", "resigned", "resignation", "launched", "launch", "signed",
    "suspended", "reopened", "restored", "reversed", "approves", "approved",
    "rejects", "rejected", "announced", "announcement", "statement", "report",
}
_STOP = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "of", "in", "on", "at", "to", "for", "from", "by", "with",
    "as", "is", "are", "was", "were", "be", "been", "being", "has", "have",
    "had", "will", "would", "could", "should", "may", "might", "can", "it",
    "its", "he", "she", "they", "them", "their", "his", "her", "we", "our",
    "you", "your", "i", "my", "me", "us", "who", "whom", "which", "what",
    "when", "where", "why", "how", "also", "more", "most", "very", "said",
    "says", "reported", "noted", "about", "into", "over", "after", "before",
    "while", "during", "between", "against", "under", "up", "out", "so",
    "such", "new", "first", "last", "one", "two", "three", "amid",
}
_WORD = re.compile(r"[A-Za-z0-9'\u2019-]+")
_CONNECTOR = {"of", "the", "and", "for", "al", "bin", "bint", "de", "da", "van"}
_DATE_LABEL = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,?\s+\d{4})?")


class SearchUnavailable(Exception):
    """The configured discovery provider cannot answer right now."""


# GDELT's published courtesy limit is one request every five seconds.
_GDELT_LAST_CALL = 0.0


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    publisher: str = ""
    published_at: datetime | None = None
    description: str = ""
    rank: int = 0


class SearchAdapter(Protocol):
    name: str
    label: str
    # Adapters may impose a tighter query budget than the newspaper profile
    # (GDELT asks for one request every five seconds).
    max_queries: int

    def available(self) -> tuple[bool, str]: ...

    def search(self, query: str, *, count: int = 8) -> list[SearchHit]: ...


@dataclass
class DiscoveryResult:
    provider: str = ""
    status: str = "unavailable"
    detail: str = ""
    queries: list[str] = field(default_factory=list)
    created: list[ExternalReference] = field(default_factory=list)
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def found(self) -> int:
        return len(self.created)


def _json_request(
    url: str,
    *,
    params: dict,
    headers: dict | None = None,
    timeout: float,
    get: Callable | None = None,
) -> dict:
    fetch = get or httpx.get
    try:
        response = fetch(
            url,
            params=params,
            headers=headers or {},
            timeout=timeout,
            follow_redirects=True,
        )
    except Exception as exc:  # network failure is an expected editorial state
        raise SearchUnavailable(f"Search provider unreachable: {type(exc).__name__}") from exc
    if response.status_code == 429:
        raise SearchUnavailable("Search provider rate limit reached; try again later.")
    if response.status_code in (401, 403):
        raise SearchUnavailable("Search provider rejected the configured credential.")
    if response.status_code >= 400:
        raise SearchUnavailable(f"Search provider returned HTTP {response.status_code}.")
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise SearchUnavailable("Search provider returned an unreadable response.") from exc
    if not isinstance(payload, dict):
        raise SearchUnavailable("Search provider returned an unexpected response shape.")
    return payload


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return as_utc(datetime.fromisoformat(text))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        return as_utc(parsedate_to_datetime(text))
    except (TypeError, ValueError):
        pass
    for pattern in ("%Y%m%dT%H%M%S", "%Y-%m-%d"):
        try:
            return as_utc(datetime.strptime(text, pattern))
        except ValueError:
            continue
    return None


class GoogleCSEAdapter:
    """Google Programmable Search JSON API. Never scrapes result pages."""

    name = "google_cse"
    label = "Google Programmable Search"
    max_queries = 5

    def __init__(
        self,
        api_key: str | None = None,
        cx: str | None = None,
        *,
        get: Callable | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.google_cse_api_key).strip()
        self.cx = (cx if cx is not None else settings.google_cse_cx).strip()
        self.get = get
        self.timeout = timeout or settings.search_timeout_seconds

    def available(self) -> tuple[bool, str]:
        if not self.api_key or not self.cx:
            return False, "Google Custom Search needs CHANNEL_PRESS_GOOGLE_CSE_API_KEY and CHANNEL_PRESS_GOOGLE_CSE_CX."
        return True, "configured"

    def search(self, query: str, *, count: int = 8) -> list[SearchHit]:
        ready, reason = self.available()
        if not ready:
            raise SearchUnavailable(reason)
        payload = _json_request(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": self.api_key,
                "cx": self.cx,
                "q": query,
                "num": max(1, min(10, count)),
                "safe": "off",
            },
            timeout=self.timeout,
            get=self.get,
        )
        hits: list[SearchHit] = []
        for index, item in enumerate(payload.get("items") or []):
            link = str(item.get("link") or "")
            if not link:
                continue
            pagemap = item.get("pagemap") or {}
            published = None
            for meta in pagemap.get("metatags") or []:
                if not isinstance(meta, dict):
                    continue
                published = _parse_datetime(
                    meta.get("article:published_time")
                    or meta.get("datePublished")
                    or meta.get("og:updated_time")
                )
                if published:
                    break
            hits.append(
                SearchHit(
                    url=link,
                    title=str(item.get("title") or ""),
                    publisher=str(item.get("displayLink") or urlsplit(link).hostname or ""),
                    published_at=published,
                    description=str(item.get("snippet") or ""),
                    rank=index,
                )
            )
        return hits


class BraveSearchAdapter:
    """Brave Search API (subscription token), a documented independent index."""

    name = "brave"
    label = "Brave Search"
    max_queries = 5

    def __init__(
        self,
        api_key: str | None = None,
        *,
        get: Callable | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else settings.brave_search_api_key).strip()
        self.get = get
        self.timeout = timeout or settings.search_timeout_seconds

    def available(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "Brave Search needs CHANNEL_PRESS_BRAVE_SEARCH_API_KEY."
        return True, "configured"

    def search(self, query: str, *, count: int = 8) -> list[SearchHit]:
        ready, reason = self.available()
        if not ready:
            raise SearchUnavailable(reason)
        payload = _json_request(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max(1, min(20, count)), "safesearch": "off"},
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
            },
            timeout=self.timeout,
            get=self.get,
        )
        results = ((payload.get("web") or {}).get("results")) or []
        hits: list[SearchHit] = []
        for index, item in enumerate(results):
            link = str(item.get("url") or "")
            if not link:
                continue
            profile = item.get("profile") or {}
            hits.append(
                SearchHit(
                    url=link,
                    title=str(item.get("title") or ""),
                    publisher=str(profile.get("long_name") or urlsplit(link).hostname or ""),
                    published_at=_parse_datetime(item.get("page_age") or item.get("age")),
                    description=str(item.get("description") or ""),
                    rank=index,
                )
            )
        return hits


class GdeltAdapter:
    """GDELT DOC 2.0 public API: keyless, documented and terms-compliant.

    GDELT asks clients to send at most one request every five seconds, so this
    adapter throttles and uses a single, most-specific query instead of the
    newspaper's full query budget.
    """

    name = "gdelt"
    label = "GDELT DOC 2.0"
    max_queries = 1

    def __init__(
        self,
        *,
        get: Callable | None = None,
        timeout: float | None = None,
    ) -> None:
        self.get = get
        self.timeout = timeout or settings.search_timeout_seconds

    def available(self) -> tuple[bool, str]:
        return True, "keyless public API (GDELT DOC 2.0)"

    def search(self, query: str, *, count: int = 8) -> list[SearchHit]:
        if self.get is None:
            global _GDELT_LAST_CALL
            elapsed = time.monotonic() - _GDELT_LAST_CALL
            if elapsed < 5.2:
                time.sleep(5.2 - elapsed)
            _GDELT_LAST_CALL = time.monotonic()
        payload = _json_request(
            "https://api.gdeltproject.org/api/v2/doc/doc",
            params={
                "query": query,
                "mode": "artlist",
                "format": "json",
                "maxrecords": max(1, min(50, count)),
                "sort": "hybridrel",
                "timespan": f"{max(1, int(settings.search_timespan_days))}d",
            },
            headers={"Accept": "application/json"},
            timeout=self.timeout,
            get=self.get,
        )
        hits: list[SearchHit] = []
        for index, item in enumerate(payload.get("articles") or []):
            link = str(item.get("url") or "")
            if not link:
                continue
            hits.append(
                SearchHit(
                    url=link,
                    title=str(item.get("title") or ""),
                    publisher=str(item.get("domain") or urlsplit(link).hostname or ""),
                    published_at=_parse_datetime(item.get("seendate")),
                    description="",
                    rank=index,
                )
            )
        return hits


class RssAdapter:
    """Keyless discovery from publisher-supplied news feeds.

    This is not a search engine: it reads each outlet's own published RSS feed
    and filters items locally by the query's factual anchors. It is a genuine,
    terms-compliant fallback when no search API is configured, and it never
    scrapes a results page.
    """

    name = "rss"
    label = "Publisher RSS feeds"
    # The feeds are fetched once and cached; extra queries only filter locally.
    max_queries = 3

    def __init__(self, *, get: Callable | None = None, timeout: float | None = None) -> None:
        self.get = get
        self.timeout = timeout or settings.search_timeout_seconds
        configured = (settings.search_rss_feeds or "").strip()
        self.feeds = [
            value.strip()
            for value in configured.split(",")
            if value.strip().startswith(("http://", "https://"))
        ] or [
            "https://www.dawn.com/feeds/home",
            "https://feeds.bbci.co.uk/news/world/rss.xml",
        ]
        self._cache: list[SearchHit] = []
        self._cached_at = 0.0

    def available(self) -> tuple[bool, str]:
        if not self.feeds:
            return False, "No publisher RSS feed is configured."
        return True, f"{len(self.feeds)} publisher feed(s)"

    def _load(self) -> list[SearchHit]:
        if self._cache and time.monotonic() - self._cached_at < 600:
            return self._cache
        feed_client = self.get or httpx.get
        hits: list[SearchHit] = []
        seen_urls: set[str] = set()
        for feed in self.feeds:
            try:
                response = feed_client(
                    feed,
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers={"User-Agent": "ChannelPressResearch/1.0 (+editorial context)"},
                )
                if response.status_code >= 400:
                    continue
                root = ElementTree.fromstring(response.content)
            except Exception:
                continue
            for index, item in enumerate(root.iter("item")):
                link = (item.findtext("link") or "").strip()
                title = (item.findtext("title") or "").strip()
                if not link or not title or link in seen_urls:
                    continue
                seen_urls.add(link)
                publisher = (urlsplit(link).hostname or "").removeprefix("www.")
                hits.append(
                    SearchHit(
                        url=link,
                        title=title,
                        publisher=publisher,
                        published_at=_parse_datetime(
                            item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date")
                        ),
                        description=_strip_markup(item.findtext("description") or "")[:500],
                        rank=index,
                    )
                )
        self._cache = hits
        self._cached_at = time.monotonic()
        return hits

    def search(self, query: str, *, count: int = 8) -> list[SearchHit]:
        terms = {
            word.lower()
            for word in _WORD.findall(query or "")
            if word.lower() not in _STOP and len(word) > 2
        }
        # A feed is not a search index. Requiring a distinctive name in the
        # headline prevents a loose keyword such as "rights" from turning an
        # unrelated story into a candidate.
        strong = {
            word.lower()
            for word in _WORD.findall(query or "")
            if word[:1].isupper() and word.lower() not in _STOP and len(word) > 2
        }
        if not terms:
            return []
        scored: list[tuple[int, SearchHit]] = []
        for hit in self._load():
            title = hit.title.lower()
            haystack = f"{title} {hit.description}".lower()
            if strong and not any(term in title for term in strong):
                continue
            score = sum(1 for term in terms if term in haystack)
            if strong or score >= 2:
                scored.append((score, hit))
        scored.sort(key=lambda item: (-item[0], item[1].rank))
        return [hit for _score, hit in scored[:count]]


def _strip_markup(value: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", value or "").split())


_ADAPTERS: dict[str, type] = {
    "google_cse": GoogleCSEAdapter,
    "brave": BraveSearchAdapter,
    "gdelt": GdeltAdapter,
    "rss": RssAdapter,
}


def build_adapter(
    preference: str = "auto",
    *,
    get: Callable | None = None,
) -> SearchAdapter:
    """Return the requested adapter, or the first available provider for auto."""
    wanted = (preference or "auto").strip().lower()
    if wanted in {"off", "none", "manual"}:
        raise SearchUnavailable("Automatic search is turned off for this newspaper.")
    if wanted in _ADAPTERS:
        return _ADAPTERS[wanted](get=get)
    order: list[type] = [GoogleCSEAdapter, BraveSearchAdapter, RssAdapter]
    first_unavailable = ""
    for factory in order:
        adapter = factory(get=get)
        ready, reason = adapter.available()
        if ready:
            return adapter
        first_unavailable = first_unavailable or reason
    raise SearchUnavailable(first_unavailable or "No search provider is configured.")


def provider_status(preference: str = "auto", *, get: Callable | None = None) -> dict:
    """Editor-facing provider state, including why a provider is unusable."""
    try:
        adapter = build_adapter(preference, get=get)
    except SearchUnavailable as exc:
        return {"status": "unavailable", "provider": "", "detail": str(exc)}
    ready, reason = adapter.available()
    return {
        "status": "ready" if ready else "unavailable",
        "provider": adapter.name,
        "label": adapter.label,
        "detail": reason,
    }


_QUERY_STOP = _STOP | {
    "there", "these", "those", "photo", "note", "update", "breaking", "reported",
    "exclusive", "watch", "read", "more", "video", "live", "news", "alert",
    "according", "reports", "report", "said", "says",
}


def _proper_names(text: str) -> list[str]:
    """Collect runs of capitalised words, keeping names such as "Rajnath Singh".

    A run stops at a lower-case word, so "Indian Defence Minister Rajnath
    Singh raised" yields the minister's full title instead of one generic
    nationality adjective.
    """
    names: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if not current:
            return
        while current and current[-1].lower() in _CONNECTOR:
            current.pop()
        value = " ".join(current).strip(" -")
        current.clear()
        if len(value) < 3 or value.isupper() or value.lower() in _QUERY_STOP:
            return
        if value not in names:
            names.append(value)

    for word in _WORD.findall(text or ""):
        if word[:1].isupper() and word.lower() not in _QUERY_STOP:
            current.append(word)
        elif word.lower() in _CONNECTOR and current:
            current.append(word.lower())
        else:
            flush()
    flush()
    return names


def build_queries(
    story: StoryCluster,
    source_texts: list[str],
    *,
    max_queries: int = 3,
) -> list[str]:
    """Derive 2-5 targeted queries from facts already present in the story.

    No country, place, date or actor is added. If the source does not name a
    location, the query does not invent one.
    """
    title = story.canonical_title or ""
    segments = [title, story.summary or "", *source_texts]
    text = "\n".join(segments)
    # Names in the headline are more distinctive than names buried in a post;
    # ``NOTE``/``UPDATE`` labels in source furniture must never become queries.
    # Each field is read separately so a trailing place name cannot merge with
    # the opening words of the next source.
    names: list[str] = []
    for segment in segments:
        for name in _proper_names(segment):
            if name not in names:
                names.append(name)
    names = names[:6]
    title_events = sorted({word for word in _EVENT_WORDS if re.search(rf"\b{word}\b", title, re.I)})
    events = title_events or sorted(
        {word for word in _EVENT_WORDS if re.search(rf"\b{word}\b", text, re.I)}
    )
    date_label = ""
    match = _DATE_LABEL.search(text)
    if match:
        date_label = match.group(0)

    def _quoted(value: str) -> str:
        return f'"{value}"' if " " in value else value

    candidates: list[str] = []
    # Use the most specific name in the headline (a five-word title or place),
    # not a generic nationality adjective such as "India".
    specific = [name for name in names if len(name.split()) <= 6]
    primary = max(specific, key=lambda name: (len(name.split()), len(name)), default=names[0] if names else "")
    event = events[0] if events else ""
    if primary and event:
        candidates.append(f"{_quoted(primary)} {event}")
    title_words = [word for word in _WORD.findall(title) if word.lower() not in _QUERY_STOP]
    if len(title_words) >= 4:
        candidates.append(" ".join(title_words[:10]))
    if primary and len(names) > 1:
        candidates.append(f"{_quoted(primary)} {_quoted(names[1])}")
    if event:
        place = next((name for name in names[1:] if name.lower() not in _EVENT_WORDS), "")
        query = f"{event} {place}".strip()
        if date_label:
            query = f"{query} {date_label}"
        candidates.append(query)

    queries: list[str] = []
    for candidate in candidates:
        value = " ".join(candidate.split()).strip(" ,")
        if len(value) < 6 or len(value) > 160:
            continue
        key = value.lower()
        if key in {existing.lower() for existing in queries}:
            continue
        # A query made only of stopwords or one common noun is noise.
        if not [word for word in _WORD.findall(value) if word.lower() not in _STOP]:
            continue
        queries.append(value)
        if len(queries) >= max(2, min(5, max_queries)):
            break
    return queries[: max(1, min(5, max_queries))]


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def _overlap(left: str, right: str) -> float:
    left_words = {word.lower() for word in _WORD.findall(left or "") if word.lower() not in _STOP}
    right_words = {word.lower() for word in _WORD.findall(right or "") if word.lower() not in _STOP}
    if not left_words or not right_words:
        return 0.0
    return len(left_words & right_words) / len(left_words | right_words)


def rank_hits(
    hits: list[SearchHit],
    story: StoryCluster,
    *,
    source_texts: list[str],
    own_handles: set[str] | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> list[SearchHit]:
    """Rank candidates by event identity, date alignment and source quality."""
    own = {handle.lstrip("@").lower() for handle in (own_handles or set())}
    reference_text = " ".join([story.canonical_title or "", *source_texts[:3]])
    scored: list[tuple[float, SearchHit]] = []
    for hit in hits:
        host = _host(hit.url)
        if not host:
            continue
        if host in SOCIAL_HOSTS or host in AGGREGATOR_HOSTS:
            continue
        if host == "t.me" or any(handle and handle in hit.url.lower() for handle in own):
            continue
        title_overlap = _overlap(hit.title, reference_text)
        # A shared generic word ("Indian", "minister") is not the same event.
        # Require a real headline match before a page can become a candidate.
        if title_overlap < 0.16 and _overlap(hit.description, reference_text) < 0.3:
            continue
        score = title_overlap * 0.55
        score += _overlap(hit.description, reference_text) * 0.15
        if hit.published_at and window_end:
            delta = abs((as_utc(hit.published_at) - as_utc(window_end)).total_seconds())
            if delta <= 3 * 86400:
                score += 0.2
            elif delta <= 10 * 86400:
                score += 0.1
            elif delta > 45 * 86400:
                score -= 0.25
        if story.risk_level == "high":
            score -= 0.05
        scored.append((score, hit))
    scored.sort(key=lambda item: (-item[0], item[1].rank))
    return [hit for _score, hit in scored]


def _existing_urls(db: Session, story_id: int) -> set[str]:
    rows = db.scalars(
        select(ExternalReference.url).where(ExternalReference.story_id == story_id)
    )
    return {str(url) for url in rows}


def _auto_acceptable(reference: ExternalReference, story: StoryCluster) -> bool:
    """A narrow automatic acceptance rule for ordinary, dated context.

    Casualty figures, allegations against named people and high-risk stories
    always stay for human review. This is context, never claim evidence.
    """
    if story.risk_level == "high":
        return False
    if not reference.reachable or not reference.published_at:
        return False
    if _host(reference.url) in SOCIAL_HOSTS | AGGREGATOR_HOSTS:
        return False
    contested = re.search(
        r"(?i)\b(?:killed|dead|death toll|injured|wounded|rape|torture|"
        r"corruption|alleged|accused|arrested)\b",
        f"{story.canonical_title} {story.summary}",
    )
    return contested is None


def discover_candidates(
    db: Session,
    story: StoryCluster,
    profile: PublicationProfile,
    *,
    adapter: SearchAdapter | None = None,
    get: Callable | None = None,
    transport: Transport | None = None,
    resolver: Callable | None = None,
    max_per_story: int | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> DiscoveryResult:
    """Search, rank, fetch and store candidate references for one story.

    Network work is bounded by the profile's query budget, the provider
    timeout and the fetcher's SSRF/size rules. The caller owns the transaction
    and should not hold a write transaction across this call.
    """
    result = DiscoveryResult()
    if (profile.research_policy or "editor_triggered") == "off":
        result.status = "disabled"
        result.detail = "Research is turned off for this newspaper."
        return result
    try:
        search = adapter or build_adapter(profile.research_provider, get=get)
    except SearchUnavailable as exc:
        result.status = "unavailable"
        result.detail = str(exc)
        return result
    ready, reason = search.available()
    result.provider = search.name
    if not ready:
        result.status = "unavailable"
        result.detail = reason
        return result

    cap = max(1, min(10, max_per_story or profile.research_max_per_story or 5))
    remaining = cap
    existing = _existing_urls(db, story.id)
    source_texts = _story_source_texts(db, story.id)
    query_budget = min(
        max(1, min(5, profile.research_queries_per_story or 3)),
        max(1, int(getattr(search, "max_queries", 5))),
    )
    queries = build_queries(story, source_texts, max_queries=query_budget)
    result.queries = queries
    if not queries:
        result.status = "no_query"
        result.detail = "The source does not contain enough factual anchors to search."
        return result

    hits: list[SearchHit] = []
    for query in queries:
        try:
            hits.extend(search.search(query, count=8))
        except SearchUnavailable as exc:
            result.errors.append(f"{query}: {exc}")
    if not hits and result.errors:
        result.status = "error"
        result.detail = result.errors[0]
        return result

    ranked = rank_hits(
        hits,
        story,
        source_texts=source_texts,
        own_handles=_own_handles(db, story),
        window_start=window_start or story.created_at,
        window_end=window_end or story.updated_at,
    )
    for hit in ranked:
        if remaining <= 0:
            break
        try:
            url = normalise_url(hit.url)
        except ResearchError:
            result.skipped += 1
            continue
        if url in existing:
            result.skipped += 1
            continue
        reference = ExternalReference(
            story_id=story.id,
            url=url[:1000],
            publisher=(hit.publisher or _host(url))[:160],
            title=hit.title[:300],
            excerpt=(hit.description or "")[:1000],
            provider=f"{SEARCH_PROVIDER_PREFIX}{search.name}",
            reachable=False,
            review_status="candidate",
            search_query=queries[0] if queries else "",
            relevance=round(_overlap(hit.title, story.canonical_title or ""), 3),
            published_at=hit.published_at,
        )
        try:
            outcome = fetch_document(url, transport=transport, resolver=resolver)
        except ResearchError as exc:
            reference.terms_note = f"Search candidate; page not fetched: {str(exc)[:220]}"
        else:
            reference.url = outcome.url[:1000]
            reference.publisher = (hit.publisher or _host(outcome.url))[:160]
            reference.title = (outcome.title or hit.title)[:300]
            reference.excerpt = outcome.excerpt
            reference.reachable = True
            reference.http_status = outcome.status
            reference.content_sha256 = outcome.content_sha256
            reference.terms_note = f"{outcome.terms_note}; discovered via {search.label}"[:300]
            if (profile.research_auto_accept or "review") == "auto_context" and _auto_acceptable(
                reference, story
            ):
                reference.review_status = "accepted"
        db.add(reference)
        db.flush()
        existing.add(reference.url)
        result.created.append(reference)
        remaining -= 1
    result.status = "ok" if result.created else "no_candidates"
    if not result.created and not result.detail:
        result.detail = "No independent candidate matched this story closely enough."
    return result


def _story_source_texts(db: Session, story_id: int) -> list[str]:
    from ..models import PostFragment, SourcePost

    rows = db.execute(
        select(PostFragment.text)
        .join(SourcePost, PostFragment.post_id == SourcePost.id)
        .where(PostFragment.story_id == story_id)
        .order_by(SourcePost.posted_at.asc())
    ).scalars()
    return [str(text) for text in rows if text]


def _own_handles(db: Session, story: StoryCluster) -> set[str]:
    from ..models import Channel

    handles: set[str] = set()
    channel = db.get(Channel, story.channel_id)
    if channel is not None and channel.handle:
        handles.add(channel.handle)
    return handles


def research_budget_used(db: Session, story_ids: list[int]) -> int:
    if not story_ids:
        return 0
    return int(
        db.scalar(
            select(func.count())
            .select_from(ExternalReference)
            .where(
                ExternalReference.story_id.in_(story_ids),
                ExternalReference.provider.like(f"{SEARCH_PROVIDER_PREFIX}%"),
            )
        )
        or 0
    )
