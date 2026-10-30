"""Bounded, terms-respecting web research for editorial context.

Research is deliberately narrow. An editor supplies a public URL, or a
configured search adapter discovers one; the service fetches one HTML document
with strict SSRF, size, redirect and robots.txt limits, extracts a short
excerpt and stores it as *candidate* provenance.

Nothing fetched here is claim evidence. A candidate never reaches the printed
appendix until an editor accepts it, and even an accepted reference remains
background context rather than proof of a fact.
"""

from __future__ import annotations

from .search import (
    DiscoveryResult,
    GdeltAdapter,
    GoogleCSEAdapter,
    BraveSearchAdapter,
    RssAdapter,
    SearchHit,
    SearchUnavailable,
    build_adapter,
    build_queries,
    discover_candidates,
    provider_status,
    rank_hits,
)
from .service import (
    RESEARCH_PROVIDER,
    ResearchError,
    ResearchOutcome,
    accept_reference,
    fetch_document,
    reject_reference,
    research_reference,
)

__all__ = [
    "RESEARCH_PROVIDER",
    "BraveSearchAdapter",
    "DiscoveryResult",
    "GdeltAdapter",
    "GoogleCSEAdapter",
    "ResearchError",
    "ResearchOutcome",
    "RssAdapter",
    "SearchHit",
    "SearchUnavailable",
    "accept_reference",
    "build_adapter",
    "build_queries",
    "discover_candidates",
    "fetch_document",
    "provider_status",
    "rank_hits",
    "reject_reference",
    "research_reference",
]
