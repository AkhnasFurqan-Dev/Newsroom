from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..ai.providers import DraftProvider, DraftResult, FixtureDraftProvider, StoryBundle, gather_bundle
from ..models import (
    Article,
    ArticleClaimLink,
    ArticleVersion,
    EditionArticle,
    ModelRun,
    StoryClaim,
    StoryCluster,
)
from ..timeutil import utcnow
from .audit import audit_article
from .claims import build_claims, contains_injection, strip_injection
from .entities import sentence_split


def _snapshot(article: Article) -> str:
    return json.dumps(
        {
            "title": article.title,
            "deck": article.deck,
            "standfirst": article.standfirst,
            "body_json": article.body_json,
            "genre": article.genre,
            "section": article.section,
            "status": article.status,
            "revision": article.revision,
        },
        ensure_ascii=False,
    )


def _clean_draft(result: DraftResult) -> DraftResult:
    title = result.title.strip()
    if contains_injection(title):
        title, _ = strip_injection(title)
    body = []
    for paragraph in result.body:
        text = paragraph.strip()
        if not text:
            continue
        if contains_injection(text):
            text, _ = strip_injection(text)
        if text:
            body.append(text)
    return DraftResult(
        title=title[:300] or "Untitled report",
        deck=result.deck.strip()[:300],
        standfirst=result.standfirst.strip(),
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
    if existing is not None:
        in_edition = db.scalar(select(EditionArticle.id).where(EditionArticle.article_id == existing.id).limit(1))
        if existing.locked or in_edition:
            return existing
        db.execute(delete(ArticleClaimLink).where(ArticleClaimLink.article_id == existing.id))
        db.delete(existing)
        db.flush()

    bundle = gather_bundle(db, story)
    if not bundle.sources:
        raise ValueError("Story has no source evidence.")
    claims = build_claims(db, story)
    bundle.claims = claims
    fallback_reason = ""
    try:
        result = provider.draft(bundle)
    except Exception as exc:
        fallback_reason = f"{type(exc).__name__}: {str(exc)[:300]}"
        result = FixtureDraftProvider().draft(bundle)
        result.fallback_reason = fallback_reason
    result = _clean_draft(result)
    if not result.body:
        raise ValueError("Draft provider returned an empty body.")

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
) -> Article:
    body = [paragraph.strip() for paragraph in body_text.split("\n\n") if paragraph.strip()]
    if not body:
        raise ValueError("Article body cannot be empty.")
    article.title = title.strip()[:300]
    article.deck = deck.strip()[:300]
    article.standfirst = standfirst.strip()
    article.body_json = json.dumps(body, ensure_ascii=False)
    article.body_text = "\n\n".join(body)
    article.word_count = len(article.body_text.split())
    article.revision += 1
    article.locked = lock or article.locked
    article.updated_at = utcnow()
    # A manual edit does not silently clear the audit; it becomes stale and is
    # re-audited explicitly by the editor action.
    db.add(
        ArticleVersion(
            article_id=article.id,
            revision=article.revision,
            snapshot_json=_snapshot(article),
            actor_id=actor_id,
        )
    )
    db.flush()
    return article


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
