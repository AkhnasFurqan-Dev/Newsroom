from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..models import ArticleClaimLink, ReviewFlag, StoryClaim
from .entities import proper_nouns, sentence_split
from .style import style_lint

GENERIC_CAPS = {
    "the", "a", "an", "no", "security", "police", "officials", "officers", "forces", "two", "three",
    "four", "five", "six", "one", "heavy", "light", "local", "another", "government", "ministry",
    "military", "army", "navy", "air", "state", "bank", "cabinet", "parliament", "committee",
    "information", "minister", "separately", "analysis", "editorial", "update", "correction",
    "the", "this", "that", "on", "in", "at", "after", "before", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "january", "february", "march", "april", "may",
    "june", "july", "august", "september", "october", "november", "december", "traders", "residents",
    "gunmen", "heavy", "machinery", "a", "an", "district", "provincial", "federal", "national",
}

NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "dozens", "hundreds", "thousands", "million", "billion",
}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("“", '"').replace("”", '"')).strip().lower()


def _quote_fragments(sentence: str) -> list[str]:
    return [q for q in re.findall(r'"([^"]{12,})"', sentence.replace("“", '"').replace("”", '"'))]


def _fact_terms(sentence: str) -> tuple[set[str], set[str], list[str]]:
    nums = set(re.findall(r"\b\d+(?:\.\d+)?\b", sentence))
    for word in NUMBER_WORDS:
        if re.search(rf"\b{word}\b", sentence.lower()):
            nums.add(word)
    nouns = {
        noun for noun in proper_nouns(sentence)
        if noun.lower() not in GENERIC_CAPS and not noun.isdigit() and len(noun) > 2
    }
    quotes = _quote_fragments(sentence)
    return nums, nouns, quotes


def audit_article(
    db: Session,
    *,
    article_id: int,
    story_id: int,
    title: str,
    deck: str,
    standfirst: str,
    body: list[str],
    corpus: str,
    claims: list[StoryClaim],
    genre: str,
    story_risk: str,
    story_risk_tags: list[str],
) -> dict:
    corpus_norm = _normalise(corpus)
    body_text = "\n".join(body)
    fact_sentences = 0
    supported = 0
    unsupported: list[dict] = []
    links: list[ArticleClaimLink] = []
    for sentence in sentence_split(body_text):
        nums, nouns, quotes = _fact_terms(sentence)
        if not nums and not nouns and not quotes:
            continue
        fact_sentences += 1
        reasons: list[str] = []
        for number in nums:
            if not re.search(rf"\b{re.escape(number)}\b", corpus_norm):
                reasons.append(f"number '{number}' is absent from source evidence")
        for noun in nouns:
            if _normalise(noun) not in corpus_norm:
                reasons.append(f"name/place '{noun}' is absent from source evidence")
        for quote in quotes:
            if _normalise(quote) not in corpus_norm:
                reasons.append("quoted text is not an exact source quote")
        best_claim = None
        best_score = 0.0
        sentence_tokens = set(re.findall(r"[a-z0-9]{3,}", sentence.lower()))
        for claim in claims:
            claim_tokens = set(re.findall(r"[a-z0-9]{3,}", claim.text.lower()))
            if not sentence_tokens or not claim_tokens:
                continue
            score = len(sentence_tokens & claim_tokens) / len(sentence_tokens | claim_tokens)
            if score > best_score:
                best_score = score
                best_claim = claim
        if best_claim is None and claims:
            best_claim = claims[0]
        if reasons:
            unsupported.append({"sentence": sentence, "reasons": reasons, "claim_id": best_claim.id if best_claim else None})
        else:
            supported += 1
        if best_claim is not None and best_score >= 0.12:
            links.append(
                ArticleClaimLink(
                    article_id=article_id,
                    claim_id=best_claim.id,
                    sentence=sentence,
                    supported=not reasons,
                )
            )
    coverage = (supported / fact_sentences) if fact_sentences else 1.0
    audit = {
        "fact_sentences": fact_sentences,
        "supported": supported,
        "unsupported": unsupported,
        "citation_coverage": round(coverage, 3),
        "unsupported_count": len(unsupported),
        "claims_available": len(claims),
        "audited_at": None,
    }
    for link in links:
        existing = db.query(ArticleClaimLink).filter(
            ArticleClaimLink.article_id == article_id,
            ArticleClaimLink.claim_id == link.claim_id,
            ArticleClaimLink.sentence == link.sentence,
        ).first()
        if existing is None:
            db.add(link)
    db.flush()

    style = style_lint(title, standfirst, body, genre=genre)
    audit["style"] = style

    # Flag elevated-risk and unsupported material; never auto-publish it.
    if unsupported:
        db.add(
            ReviewFlag(
                article_id=article_id,
                story_id=story_id,
                category="unsupported_claim",
                severity="block_publish",
                message=f"{len(unsupported)} factual sentence(s) could not be traced to source evidence.",
            )
        )
    if story_risk == "high":
        db.add(
            ReviewFlag(
                article_id=article_id,
                story_id=story_id,
                category="elevated_risk",
                severity="review",
                message="Elevated-risk story: " + ("; ".join(story_risk_tags) or "sensitive content"),
            )
        )
    unverified = [c for c in claims if c.certainty == "unverified"]
    if unverified:
        db.add(
            ReviewFlag(
                article_id=article_id,
                story_id=story_id,
                category="unverified_claim",
                severity="review",
                message="Draft relies on an anonymous or explicitly unverified claim. Check attribution before release.",
            )
        )
    if style["stock_phrases"]:
        db.add(
            ReviewFlag(
                article_id=article_id,
                story_id=story_id,
                category="style",
                severity="note",
                message="Stock transition phrase found by deterministic style lint.",
            )
        )
    if genre == "opinion" and not story_risk_tags:
        audit["genre_label"] = "opinion"
    return audit


def publish_gate(db: Session, article_id: int) -> tuple[bool, list[str]]:
    flags = db.query(ReviewFlag).filter(
        ReviewFlag.article_id == article_id, ReviewFlag.status == "open"
    ).all()
    blockers = [
        f.category
        for f in flags
        if f.severity in {"block_publish", "review"}
    ]
    return (len(blockers) == 0, sorted(set(blockers)))


def audit_publication(db: Session, article_id: int) -> dict:
    flags = db.query(ReviewFlag).filter(ReviewFlag.article_id == article_id).all()
    return {
        "open": sum(1 for f in flags if f.status == "open"),
        "blockers": [f.category for f in flags if f.status == "open" and f.severity in {"block_publish", "review"}],
        "all": [{"category": f.category, "severity": f.severity, "status": f.status} for f in flags],
    }
