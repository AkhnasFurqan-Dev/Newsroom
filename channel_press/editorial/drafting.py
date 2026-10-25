from __future__ import annotations

import json
import re
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..ai.providers import DraftProvider, DraftResult, FixtureDraftProvider, gather_bundle
from ..models import (
    Article,
    ArticleClaimLink,
    ArticleVersion,
    Channel,
    EditionArticle,
    ModelRun,
    PostFragment,
    PublicationProfile,
    ReviewFlag,
    SourcePost,
    StoryClaim,
    StoryCluster,
)
from ..timeutil import utcnow
from .audit import audit_article
from .claims import build_claims, contains_injection, strip_injection
from .moderation import attribution_issues, profanity_details, redact_profanity


def _snapshot(article: Article) -> str:
    return json.dumps(
        {
            "title": article.title,
            "deck": article.deck,
            "standfirst": article.standfirst,
            "body_json": article.body_json,
            "byline": article.byline,
            "genre": article.genre,
            "section": article.section,
            "status": article.status,
            "revision": article.revision,
        },
        ensure_ascii=False,
    )


_MESSAGE_WITH_TIME = re.compile(
    r"(?:[,\s]+(?:according to|as per)\s+)?(?:[,\s]+from\s+)?\bmessage\s+\d+"
    r"(?:\s+(?:at|from|on))?\s+\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
    re.I,
)
_MESSAGE_REF = re.compile(
    r"(?:[,\s]+(?:according to|as per)\s+)?(?:[,\s]+from\s+)?\bmessage\s+\d+\b",
    re.I,
)
_EVIDENCE_MARKER = re.compile(r"\[(?:S|C)\d+\]")
_ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")
# A bare ISO calendar date also leaks out of the evidence bundle; print the
# newspaper form ("20 October 2026") instead.
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _human_date(match: re.Match[str]) -> str:
    try:
        day = date.fromisoformat(match.group(0)[:10])
    except ValueError:
        return match.group(0)
    return f"{day.day} {day.strftime('%B %Y')}"


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _clean_paragraph(text: str) -> str:
    """Strip model-echoed source plumbing without touching the facts."""
    text = _EVIDENCE_MARKER.sub("", text)
    text = _MESSAGE_WITH_TIME.sub("", text)
    text = _MESSAGE_REF.sub("", text)
    text = _ISO_TIMESTAMP.sub(_human_date, text)
    text = _ISO_DATE.sub(_human_date, text)
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    sentences: list[str] = []
    seen: set[str] = set()
    for sentence in _SENTENCE_BOUNDARY.split(text):
        sentence = sentence.strip()
        key = _normalise(sentence)
        if sentence and key and key not in seen:
            seen.add(key)
            sentences.append(sentence)
    return " ".join(sentences).strip()


def _similar(a: str, b: str) -> bool:
    aw = set(_normalise(a).split())
    bw = set(_normalise(b).split())
    if not aw or not bw:
        return False
    return len(aw & bw) / len(aw | bw) >= 0.7


def _containment(short: str, long: str) -> float:
    """Share of the short text's words that also appear in the long text."""
    sw = set(_normalise(short).split())
    lw = set(_normalise(long).split())
    if not sw:
        return 0.0
    return len(sw & lw) / len(sw)


def _clean_draft(result: DraftResult) -> DraftResult:
    title = result.title.strip()
    if contains_injection(title):
        title, _ = strip_injection(title)
    standfirst = result.standfirst.strip()
    deck = result.deck.strip()[:300]
    if deck and standfirst and _similar(deck, standfirst):
        deck = ""
    seen_paragraphs: set[str] = set()
    body = []
    for paragraph in result.body:
        text = _clean_paragraph(paragraph)
        if not text:
            continue
        if contains_injection(text):
            text, _ = strip_injection(text)
        key = _normalise(text)
        if not key or key in seen_paragraphs:
            continue
        seen_paragraphs.add(key)
        body.append(text)
    # A final paragraph with no terminal punctuation is a truncated generation,
    # not a deliberate newspaper fragment. Never print half a sentence.
    if body and not re.search(r"[.!?\u2026\"'\u201d\u2019)]$", body[-1]):
        body.pop()
    # A standfirst that restates the lead adds nothing: print the attributed
    # lead only, rather than the same development twice in a row.
    if standfirst and body and _containment(standfirst, body[0]) >= 0.75:
        standfirst = ""
    # Same rule for the deck: a deck that repeats the opening sentence is
    # printed twice in the same block.
    if deck and body and _containment(deck, body[0]) >= 0.8:
        deck = ""
    return DraftResult(
        title=title[:300] or "Untitled report",
        deck=deck,
        standfirst=standfirst,
        body=body,
        provider=result.provider,
        model=result.model,
        prompt_version=result.prompt_version,
        latency_ms=result.latency_ms,
        fallback_reason=result.fallback_reason,
        raw=result.raw,
    )


def draft_story(
    db: Session,
    story: StoryCluster,
    provider: DraftProvider,
    *,
    job_id: int | None = None,
    actor_id: int | None = None,
) -> Article:
    existing = db.scalar(select(Article).where(Article.story_id == story.id).order_by(Article.revision.desc()))
    existing_audit = json.loads(existing.audit_json or "{}") if existing is not None else {}
    requires_real_redraft = (
        provider.name == "ollama"
        and existing is not None
        and existing_audit.get("provider") != "ollama"
    )
    if existing is not None:
        in_edition = db.scalar(select(EditionArticle.id).where(EditionArticle.article_id == existing.id).limit(1))
        if not requires_real_redraft and (existing.locked or in_edition or (job_id and db.info.get("durable_worker"))):
            return existing
        if requires_real_redraft:
            db.execute(delete(EditionArticle).where(EditionArticle.article_id == existing.id))
        db.execute(delete(ArticleClaimLink).where(ArticleClaimLink.article_id == existing.id))
        db.delete(existing)
        db.flush()

    bundle = gather_bundle(db, story)
    if not bundle.sources:
        raise ValueError("Story has no source evidence.")
    claims = build_claims(db, story)
    bundle.claims = claims
    fallback_reason = ""
    if db.info.get("durable_worker"):
        db.commit()
    try:
        result = provider.draft(bundle)
    except Exception as exc:
        channel = db.get(Channel, story.channel_id)
        if channel is not None and not channel.is_demo:
            raise RuntimeError(
                f"Ollama drafting failed for real channel story {story.id}: "
                f"{type(exc).__name__}: {str(exc)[:300]}"
            ) from exc
        fallback_reason = f"{type(exc).__name__}: {str(exc)[:300]}"
        result = FixtureDraftProvider().draft(bundle)
        result.fallback_reason = fallback_reason
    result = _clean_draft(result)
    if not result.body:
        raise ValueError("Draft provider returned an empty body.")

    # Draft-output profanity validation: headlines and decks never print
    # profanity (quoted or not); body text keeps quoted evidence so the editor
    # can judge whether the quote is editorially necessary. The system must not
    # generate gratuitous profanity even when the source post contains it.
    redacted: list[str] = []
    result.title, removed = redact_profanity(result.title, keep_quotes=False)
    redacted.extend(removed)
    result.deck, removed = redact_profanity(result.deck, keep_quotes=False)
    redacted.extend(removed)
    result.standfirst, removed = redact_profanity(result.standfirst, keep_quotes=False)
    redacted.extend(removed)
    cleaned_body: list[str] = []
    for paragraph in result.body:
        paragraph, removed = redact_profanity(paragraph)
        redacted.extend(removed)
        cleaned_body.append(paragraph)
    result.body = cleaned_body

    body_json = json.dumps(result.body, ensure_ascii=False)
    body_text = "\n\n".join(result.body)
    word_count = len(body_text.split())
    article = Article(
        story_id=story.id,
        title=result.title,
        deck=result.deck,
        standfirst=result.standfirst,
        body_json=body_json,
        body_text=body_text,
        genre=story.genre if story.genre in {"news", "analysis", "opinion", "editor_note", "brief"} else "news",
        section=story.section,
        status="draft",
        locked=False,
        risk_level=story.risk_level,
        risk_flags_json=story.entity_json,
        word_count=word_count,
        revision=1,
        created_by=actor_id,
    )
    db.add(article)
    db.flush()
    channel = db.get(Channel, story.channel_id)
    output_text = " ".join([result.title, result.deck, result.standfirst, *result.body])
    moderation = profanity_details(output_text)
    if moderation["level"] != "none":
        article.status = "needs_review"
        message = f"Draft language moderation: {moderation['level']}."
        if redacted:
            message += " Redacted from draft: " + ", ".join(sorted(set(redacted))) + "."
        if moderation["quoted"]:
            message += " Quoted profanity was preserved in the evidence and body for editorial decision."
        if moderation["needs_review"]:
            message += " Targeted abuse or threats must be reviewed by a human before release."
        db.add(
            ReviewFlag(
                article_id=article.id,
                story_id=story.id,
                category="profanity",
                severity=(
                    "block_publish"
                    if moderation["level"] in {"threat", "hate_or_dehumanizing"}
                    else "review"
                ),
                message=message[:1000],
            )
        )
    profile = db.get(PublicationProfile, 1) or db.scalar(select(PublicationProfile).limit(1))
    publications = {
        name.strip()
        for name in (channel.title if channel else "", profile.name if profile else "")
        if name and name.strip()
    }
    attribution_findings: list[str] = []
    for publication in publications:
        attribution_findings.extend(attribution_issues(output_text, publication))
    if attribution_findings:
        article.status = "needs_review"
        db.add(
            ReviewFlag(
                article_id=article.id,
                story_id=story.id,
                category="attribution",
                severity="review",
                message=(
                    "Publication identity was written as a person or eyewitness: "
                    + "; ".join(sorted(set(attribution_findings)))
                )[:1000],
            )
        )
    # Source-side moderation is escalated to the human queue even when the
    # draft itself is clean: a threat or hate claim must never be auto-released
    # merely because a model paraphrased it politely.
    source_posts = list(
        db.scalars(
            select(SourcePost)
            .join(PostFragment, PostFragment.post_id == SourcePost.id)
            .where(PostFragment.story_id == story.id)
            .distinct()
        )
    )
    severe = [
        post
        for post in source_posts
        if post.moderation_status in {"threat", "hate_or_dehumanizing", "targeted_abuse", "sexual_or_explicit"}
    ]
    if severe:
        block = any(post.moderation_status in {"threat", "hate_or_dehumanizing"} for post in severe)
        db.add(
            ReviewFlag(
                article_id=article.id,
                story_id=story.id,
                category="source_moderation",
                severity="block_publish" if block else "review",
                message=(
                    f"{len(severe)} source post(s) contain abuse, threats or explicit material "
                    f"({', '.join(sorted({post.moderation_status for post in severe}))}). "
                    "Verify that the drafted wording attributes and paraphrases it responsibly."
                )[:1000],
            )
        )
    if any(post.classification == "language_needs_review" for post in source_posts):
        db.add(
            ReviewFlag(
                article_id=article.id,
                story_id=story.id,
                category="language_review",
                severity="review",
                message="At least one source post uses an unrecognised script; verify the translation and names.",
            )
        )
    corpus = "\n".join(source.text for source in bundle.sources)
    audit = audit_article(
        db,
        article_id=article.id,
        story_id=story.id,
        title=result.title,
        deck=result.deck,
        standfirst=result.standfirst,
        body=result.body,
        corpus=corpus,
        claims=claims,
        genre=article.genre,
        story_risk=story.risk_level,
        story_risk_tags=_risk_tags(story),
    )
    audit["audited_at"] = utcnow().isoformat()
    audit["provider"] = result.provider
    audit["model"] = result.model
    audit["prompt_version"] = result.prompt_version
    audit["fallback_reason"] = fallback_reason
    article.audit_json = json.dumps(audit, ensure_ascii=False, default=str)
    article.style_metrics_json = json.dumps(audit.get("style", {}), ensure_ascii=False)
    if audit.get("unsupported"):
        article.status = "needs_review"
        article.risk_level = "high"
    elif story.risk_level == "high":
        article.status = "needs_review"
    db.add(
        ArticleVersion(
            article_id=article.id,
            revision=1,
            snapshot_json=_snapshot(article),
            actor_id=actor_id,
        )
    )
    db.add(
        ModelRun(
            job_id=job_id,
            task="news_article" if article.genre == "news" else "analysis_article",
            provider=result.provider,
            model=result.model,
            prompt_version=result.prompt_version,
            input_hash=__import__("hashlib").sha256(corpus.encode("utf-8")).hexdigest(),
            output_hash=__import__("hashlib").sha256(body_text.encode("utf-8")).hexdigest(),
            valid=not bool(fallback_reason),
            latency_ms=result.latency_ms,
            error=fallback_reason,
        )
    )
    db.flush()
    return article


def _risk_tags(story: StoryCluster) -> list[str]:
    try:
        data = json.loads(story.entity_json or "{}")
        return list(data.get("risk_tags") or [])
    except json.JSONDecodeError:
        return []


def save_editor_edit(
    db: Session,
    article: Article,
    *,
    title: str,
    deck: str,
    standfirst: str,
    body_text: str,
    actor_id: int | None,
    lock: bool = False,
    expected_revision: int | None = None,
    byline: str | None = None,
) -> Article:
    """Compatibility wrapper around the revision service.

    Every caller (web, tests, CLI) lands on the same optimistic-concurrency
    implementation, so an editor cannot silently overwrite a newer revision.
    """
    from .revisions import save_article_revision

    return save_article_revision(
        db,
        article,
        title=title,
        deck=deck,
        standfirst=standfirst,
        body_text=body_text,
        actor_id=actor_id,
        expected_revision=expected_revision,
        byline=byline,
        lock=lock or article.locked,
    )


def reaudit_article(db: Session, article: Article) -> dict:
    story = db.get(StoryCluster, article.story_id)
    bundle = gather_bundle(db, story) if story else None
    if not bundle:
        return {}
    claims = list(db.scalars(select(StoryClaim).where(StoryClaim.story_id == article.story_id)))
    db.execute(delete(ArticleClaimLink).where(ArticleClaimLink.article_id == article.id))
    body = json.loads(article.body_json or "[]")
    corpus = "\n".join(source.text for source in bundle.sources)
    audit = audit_article(
        db,
        article_id=article.id,
        story_id=article.story_id,
        title=article.title,
        deck=article.deck,
        standfirst=article.standfirst,
        body=body,
        corpus=corpus,
        claims=claims,
        genre=article.genre,
        story_risk=article.risk_level,
        story_risk_tags=_risk_tags(story) if story else [],
    )
    audit["audited_at"] = utcnow().isoformat()
    article.audit_json = json.dumps(audit, ensure_ascii=False, default=str)
    db.flush()
    return audit
