from __future__ import annotations

import json
import re

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import ExternalReference, PostFragment, SourcePost, StoryClaim, StoryCluster, StoryTimelineEvent
from ..ingestion.adapters import extract_links
from .entities import numbers, sentence_split

INJECTION_PATTERNS = [
    r"(?i)ignore (all )?(previous|prior|following|these|the) instructions",
    r"(?i)ignore (all )?(the )?(above|system|rules)",
    r"(?i)forget (all )?(previous )?instructions",
    r"(?i)پرانی\s*ہدایات\s*کو?\s*(نظرانداز|بھولو)",
    r"(?i)(purani|pichli)\s+(hidayat|instructions)\s+ko\s+(bhool|nazar\s+andaz)",
    r"(?i)reveal (the )?(api|secret|password|token|key)",
    r"(?i)publish (the )?(administrator|admin) password",
    r"(?i)system prompt",
    r"(?i)you are now (an?|the) ",
    r"(?i)disregard (all )?(previous|prior)",
    r"(?i)send (the )?(api key|credentials)",
]


def injection_sentences(text: str) -> list[str]:
    return [s for s in sentence_split(text) if any(re.search(pattern, s) for pattern in INJECTION_PATTERNS)]


def contains_injection(text: str) -> bool:
    return bool(injection_sentences(text))


def strip_injection(text: str) -> tuple[str, list[str]]:
    flagged = injection_sentences(text)
    if not flagged:
        return text, []
    filtered = [s for s in sentence_split(text) if not any(re.search(p, s) for p in INJECTION_PATTERNS)]
    return " ".join(filtered), flagged


CASUALTY_TERMS = {"killed", "dead", "wounded", "injured", "casualties", "death"}
UNVERIFIED_TERMS = {
    "could not be independently corroborated",
    "could not be independently verified",
    "not immediately available",
    "on condition of anonymity",
    "unconfirmed",
}
CORRECTION_TERMS = {"correction", "correcting", "corrected", "withdraws", "withdrew"}


RISK_TAGS = {
    "casualties": ["killed", "dead", "wounded", "wounding", "injured", "casualties", "death"],
    "allegation": ["alleged", "accused", "corruption", "bribery", "abuse"],
    "operational": ["coordinates", "convoy route", "troop movement", "tactical position"],
    "graphic": ["beheaded", "mutilated", "execution video"],
    "minors": ["child", "children", "minor"],
    "sensitive_personal": ["home address", "phone number", "passport number"],
}


def _risk_tag(sentence: str) -> str:
    lower = sentence.lower()
    casualty_text = re.sub(
        r"\b(no|without)\s+(reported\s+)?(casualties|injuries|deaths|fatalities)\b", "", lower
    )
    casualty_text = re.sub(r"\bno one (was )?(killed|injured|hurt)\b", "", casualty_text)
    for tag, terms in RISK_TAGS.items():
        haystack = casualty_text if tag == "casualties" else lower
        if any(term in haystack for term in terms):
            return tag
    return ""


def build_claims(db: Session, story: StoryCluster) -> list[StoryClaim]:
    db.execute(delete(StoryClaim).where(StoryClaim.story_id == story.id))
    db.execute(delete(StoryTimelineEvent).where(StoryTimelineEvent.story_id == story.id))
    db.flush()
    rows = db.execute(
        select(PostFragment, SourcePost)
        .join(SourcePost, PostFragment.post_id == SourcePost.id)
        .where(PostFragment.story_id == story.id, SourcePost.inclusion_status == "included")
        .order_by(SourcePost.posted_at.asc(), PostFragment.sequence.asc())
    ).all()
    claims: list[StoryClaim] = []
    prior: list[StoryClaim] = []
    timeline_seen: set[tuple[int, int]] = set()
    for fragment, post in rows:
        text = fragment.text
        if contains_injection(text):
            text, _ = strip_injection(text)
        sentences = sentence_split(text)
        if not sentences:
            continue
        for sentence in sentences:
            if len(sentence.split()) < 5:
                continue
            lower = sentence.lower()
            numbers_found = numbers(sentence)
            claim_type = "statement"
            if any(term in lower for term in CORRECTION_TERMS):
                claim_type = "correction"
            elif any(term in lower for term in CASUALTY_TERMS) and numbers_found:
                claim_type = "casualty"
            elif '"' in sentence or "“" in sentence:
                claim_type = "quote"
            certainty = "attributed" if re.search(r"(said|says|according to|statement|confirmed|announced)", lower) else "reported"
            if any(term in lower for term in UNVERIFIED_TERMS):
                certainty = "unverified"
            status = "unverified" if certainty == "unverified" else (
                "official_statement" if re.search(r"(ministry|military|police|department|government|cabinet|bank|committee)", lower)
                else "single_source"
            )
            correction_of: int | None = None
            contradictions: list[str] = []
            if claim_type == "correction":
                # Match an earlier claim with a numeric figure quoted in this correction.
                for old in reversed(prior):
                    old_numbers = numbers(old.text)
                    if old_numbers and numbers_found and (old_numbers & numbers_found):
                        old.verification_status = "superseded"
                        old.contradictions_json = json.dumps(
                            [{"superseded_by_span": sentence[:220], "reason": "later correction"}],
                            ensure_ascii=False,
                        )
                        correction_of = old.id
                        contradictions.append(f"supersedes claim {old.id}")
                        break
            claim = StoryClaim(
                story_id=story.id,
                text=sentence,
                claim_type=claim_type,
                certainty=certainty,
                verification_status=status,
                source_post_id=post.id,
                source_fragment_id=fragment.id,
                source_span=sentence[:500],
                event_at=post.posted_at,
                correction_of_id=correction_of,
                contradictions_json=json.dumps(contradictions, ensure_ascii=False),
                risk_tag=_risk_tag(sentence),
            )
            db.add(claim)
            db.flush()
            claims.append(claim)
            prior.append(claim)
        key = (post.id, fragment.sequence)
        if key not in timeline_seen:
            timeline_seen.add(key)
            description = sentences[0]
            certainty = "attributed" if re.search(r"(said|according|statement)", description.lower()) else "reported"
            if any(term in description.lower() for term in CORRECTION_TERMS):
                description = "Correction published: " + description
                certainty = "corrected"
            db.add(
                StoryTimelineEvent(
                    story_id=story.id,
                    event_at=post.posted_at,
                    description=description[:400],
                    source_post_id=post.id,
                    certainty=certainty,
                )
            )
        for link in extract_links(text):
            exists = db.scalar(
                select(ExternalReference).where(
                    ExternalReference.story_id == story.id, ExternalReference.url == link
                )
            )
            if not exists:
                db.add(
                    ExternalReference(
                        story_id=story.id,
                        url=link[:1000],
                        publisher="Link cited in source post",
                        title="",
                        excerpt="",
                        provider="post_link",
                        reachable=False,
                    )
                )
    db.flush()
    return claims


def claim_summary(claims: list[StoryClaim]) -> dict:
    return {
        "total": len(claims),
        "superseded": sum(1 for c in claims if c.verification_status == "superseded"),
        "unverified": sum(1 for c in claims if c.verification_status == "unverified"),
        "corrections": sum(1 for c in claims if c.claim_type == "correction"),
        "risk_tagged": sum(1 for c in claims if c.risk_tag),
    }
