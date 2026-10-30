"""Decide whether a printed chronology is editorially justified.

A timeline is a strong claim about how an event developed. Two channel posts
that repeat the same bulletin are not a chronology, and a sequence of routine
updates is not a staged event. This module therefore requires *distinct,
materially different stages* before the layout may print one.

The rules are deterministic and conservative. ``show_chronology`` on the
publication profile controls the policy:

``off``
    Never print a chronology.
``on``
    Print every distinct, non-duplicate dated entry the editor has asked for.
``auto`` (default)
    Print only a genuinely staged sequence: at least three distinct entries,
    at least two different stages, and either two separate dates or three
    stages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .clean_text import is_mostly_decorative, is_near_duplicate, strip_output_decor

# Each stage groups verbs that describe a different phase of an event. A
# chronology needs evidence of more than one phase, not more than one message.
_STAGE_TERMS: dict[str, tuple[str, ...]] = {
    "incident": (
        "attack", "blast", "explosion", "fire", "flood", "quake", "earthquake",
        "crash", "accident", "collapse", "killed", "injured", "wounded",
        "shot", "clash", "storm", "landslide", "derail", "hijack", "strike",
    ),
    "response": (
        "rescue", "evacuat", "operation", "deployed", "arrest", "detained",
        "raid", "recover", "treated", "hospital", "ambulance", "relief",
        "cleared", "reopened", "restored", "repaired",
    ),
    "statement": (
        "said", "says", "stated", "announced", "confirmed", "denied", "claimed",
        "declared", "warned", "urged", "appealed", "ordered", "notified",
        "statement", "spokesman", "spokesperson", "briefing",
    ),
    "measure": (
        "ban", "banned", "curfew", "restriction", "closed", "closure",
        "suspended", "resumed", "agreement", "signed", "approved", "rejected",
        "sanction", "inquiry", "investigation", "committee", "court", "verdict",
        "sentenced", "resigned", "appointed", "elected",
    ),
    "consequence": (
        "died", "death", "funeral", "protest", "demonstration", "strike",
        "shortage", "outage", "damage", "loss", "compensation", "aid",
    ),
}


@dataclass(frozen=True)
class ChronologyEntry:
    when: datetime
    text: str
    stages: frozenset[str]


def _stages(text: str) -> frozenset[str]:
    lowered = f" {text.lower()} "
    found = {
        stage
        for stage, terms in _STAGE_TERMS.items()
        if any(f" {term}" in lowered for term in terms)
    }
    return frozenset(found)


def distinct_entries(events) -> list[ChronologyEntry]:
    """Clean, de-duplicate and stage-label dated entries, in time order."""
    ordered = sorted(events, key=lambda event: event.event_at)
    entries: list[ChronologyEntry] = []
    for event in ordered:
        text = strip_output_decor(event.description)
        if not text or is_mostly_decorative(text):
            continue
        if any(is_near_duplicate(text, existing.text) for existing in entries):
            continue
        entries.append(
            ChronologyEntry(
                when=event.event_at,
                text=text,
                stages=_stages(text),
            )
        )
    return entries


def chronology_allowed(entries: list[ChronologyEntry], policy: str) -> bool:
    """Apply the publication's chronology policy to already-distinct entries."""
    policy = (policy or "auto").strip().lower()
    if policy == "off":
        return False
    if len(entries) < 2:
        return False
    if policy == "on":
        return True
    stages = set().union(*(entry.stages for entry in entries)) if entries else set()
    dates = {entry.when.date() for entry in entries}
    if len(entries) < 3 or len(stages) < 2:
        return False
    return len(dates) >= 2 or len(stages) >= 3
