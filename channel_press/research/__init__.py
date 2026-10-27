"""Bounded, terms-respecting web research for editorial context.

Research is deliberately narrow. An editor supplies a public URL; the service
fetches one HTML document with strict SSRF, size, redirect and robots.txt
limits, extracts a short excerpt and stores it as *candidate* provenance.

Nothing fetched here is claim evidence. A candidate never reaches the printed
appendix until an editor accepts it, and even an accepted reference remains
background context rather than proof of a fact.
"""

from __future__ import annotations

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
    "ResearchError",
    "ResearchOutcome",
    "accept_reference",
    "fetch_document",
    "reject_reference",
    "research_reference",
]
