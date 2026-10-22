"""End-to-end edition pipeline shared by the CLI, scheduler and tests.

The pipeline is deliberately linear and transactional:

    rights check -> source window -> draft cleanup -> clustering -> claims
    -> drafting + independent audit -> story packaging -> layout -> PDF/QA
    -> e-paper export -> edition record

Every stage writes only to the caller's session. A caller that raises causes the
whole run to roll back, so a retried job cannot leave a half-built edition.
Quality warnings stop publication before the atomic export unless explicitly
allowed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai.providers import (
    DraftProvider,
    FixtureDraftProvider,
    OllamaDraftProvider,
    StoryBundle,
)
from .auth import active_rights
from .config import settings
from .editorial.clustering import build_stories, clear_editorial_drafts
from .editorial.drafting import draft_story
from .models import (
    Article,
    Channel,
    Edition,
    EditionArticle,
    PublicationProfile,
    ReviewFlag,
    StoryCluster,
)
from .publishing.service import PublishResult, publish_edition
from .timeutil import utcnow

ProgressCallback = Callable[[str, str], None]


class PipelineError(RuntimeError):
    pass


class RightsError(PipelineError):
    pass


@dataclass
class PipelineRequest:
    channel: Channel
    window_start: datetime
    window_end: datetime
    cutoff_at: datetime
    target_release_at: datetime
    kind: str = "manual"
    title: str = ""
    issue_number: int | None = None
    volume: int | None = None
    provider: DraftProvider | None = None
    actor_id: int | None = None
    job_id: int | None = None
    publish: bool = True
    mark_published: bool = True
    allow_quality_warnings: bool = False
    demo_edition: bool | None = None
    output_root: Path | None = None
    progress: ProgressCallback | None = None
    source_label: str = "manual"


@dataclass
class PipelineResult:
    edition: Edition
    stories: list[StoryCluster] = field(default_factory=list)
    articles: list[Article] = field(default_factory=list)
    publish_result: PublishResult | None = None
    provider: str = ""
    provider_note: str = ""
    review_count: int = 0
    flags: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    stats: dict = field(default_factory=dict)


def next_issue_number(db: Session, channel_id: int, kind: str) -> int:
    value = db.scalar(
        select(func.max(Edition.issue_number)).where(
            Edition.channel_id == channel_id, Edition.kind == kind
        )
    )
    return int(value or 0) + 1


def next_volume(db: Session, channel_id: int) -> int:
    profile = db.get(PublicationProfile, 1) or db.scalar(select(PublicationProfile).limit(1))
    return int(profile.issue_volume if profile else 1)


def select_provider(channel: Channel, requested: DraftProvider | None = None) -> tuple[DraftProvider, str]:
    if requested is not None:
        return requested, ""
    if channel.is_demo:
        return FixtureDraftProvider(), "fixture deterministic composer"
    candidate = OllamaDraftProvider()
    available, message = candidate.available()
    if available:
        return candidate, f"ollama {candidate.model}"
    # Falling back is safe because every draft still passes the independent
    # deterministic audit. The reason is recorded in the result and model runs.
    return (
        FixtureDraftProvider(),
        f"Ollama unavailable ({message}); deterministic source composer used",
    )


def _notify(request: PipelineRequest, stage: str, detail: str = "") -> None:
    if request.progress:
        request.progress(stage, detail)


def _open_flags(db: Session, article_ids: list[int]) -> list[ReviewFlag]:
    if not article_ids:
        return []
    return list(
        db.scalars(
            select(ReviewFlag).where(
                ReviewFlag.article_id.in_(article_ids),
                ReviewFlag.status == "open",
                ReviewFlag.severity.in_(["block_publish", "review"]),
            )
        )
    )


def run_pipeline(db: Session, request: PipelineRequest) -> PipelineResult:
    started = time.monotonic()
    channel = request.channel
    if request.window_start >= request.window_end:
        raise PipelineError("Edition window start must be before window end.")
    if not channel.is_demo and active_rights(db, channel.id) is None:
        raise RightsError(
            "This channel has no active content-rights declaration. Importing and AI processing "
            "are blocked until an owner records a grant."
        )

    profile = db.get(PublicationProfile, 1) or db.scalar(select(PublicationProfile).limit(1))
    if profile is None:
        raise PipelineError("Publication profile is missing; run setup first.")

    issue_number = request.issue_number or next_issue_number(db, channel.id, request.kind)
    volume = request.volume or next_volume(db, channel.id)
    title = request.title.strip() or f"{profile.name} — {request.kind.replace('_', ' ').title()}"
    edition = Edition(
        channel_id=channel.id,
        kind=request.kind,
        title=title[:240],
        volume=volume,
        issue_number=issue_number,
        status="draft",
        window_start=request.window_start,
        window_end=request.window_end,
        cutoff_at=request.cutoff_at,
        target_release_at=request.target_release_at,
        timezone=profile.timezone,
        version=1,
        profile_snapshot_json=json.dumps(
            {
                "name": profile.name,
                "handle": profile.channel_handle,
                "city": profile.city,
                "timezone": profile.timezone,
                "accent": profile.accent,
                "paper_size": profile.paper_size,
                "map_profile": profile.map_profile,
                "qr_strategy": profile.qr_strategy,
            },
            ensure_ascii=False,
        ),
        qr_policy=profile.qr_strategy,
        created_by=request.actor_id,
    )
    db.add(edition)
    db.flush()

    _notify(request, "normalization", "Clearing unpublished drafts for the window")
    cleared = clear_editorial_drafts(db, channel.id, request.window_start, request.window_end)

    _notify(request, "clustering", "Building story clusters from source fragments")
    stories = build_stories(db, channel.id, request.window_start, request.window_end, job_id=request.job_id)
    if not stories:
        raise PipelineError(
            "No includable source fragments were found in this window. Import authorized posts "
            "or widen the reporting window."
        )

    provider, provider_note = select_provider(channel, request.provider)
    _notify(request, "drafting", f"Drafting {len(stories)} stories with {getattr(provider, 'name', 'provider')}")
    articles: list[Article] = []
    draft_errors: list[str] = []
    for story in stories:
        try:
            article = draft_story(
                db,
                story,
                provider,
                job_id=request.job_id,
                actor_id=request.actor_id,
            )
            articles.append(article)
        except Exception as exc:  # one bad story must not lose a valid edition
            draft_errors.append(f"story {story.id}: {type(exc).__name__}: {str(exc)[:200]}")

    if not articles:
        raise PipelineError("No articles could be drafted from this window. " + "; ".join(draft_errors[:3]))

    _notify(request, "auditing", "Checking draft claims against source evidence")
    review_articles = [article for article in articles if article.status == "needs_review"]
    flag_rows = _open_flags(db, [article.id for article in articles])

    # Link the chosen articles to this edition explicitly so layout cannot pick
    # an article outside the window on a later re-render.
    story_importance = {story.id: story.importance for story in stories}
    ordered = sorted(
        articles,
        key=lambda item: (-story_importance.get(item.story_id, 0.0), item.id),
    )
    front_article = next((item for item in ordered if item.genre == "news"), ordered[0])
    for index, article in enumerate(ordered):
        db.add(
            EditionArticle(
                edition_id=edition.id,
                article_id=article.id,
                section=article.section,
                sort_order=index,
                is_front=article.id == front_article.id,
            )
        )
    db.flush()

    _notify(request, "layout", "Composing tabloid pages and rendering the PDF")
    publish_result: PublishResult | None = None
    if request.publish:
        publish_result = publish_edition(
            db,
            edition,
            output_root=request.output_root,
            demo_edition=request.demo_edition,
            actor_id=request.actor_id,
            allow_quality_warnings=request.allow_quality_warnings,
            mark_published=request.mark_published,
        )
        _notify(request, "quality_check", f"Rendered {publish_result.output_dir.name}")
    else:
        edition.status = "draft"
        db.flush()

    flags = [
        f"{flag.category}: {flag.message[:160]}"
        for flag in flag_rows
    ] + draft_errors
    result = PipelineResult(
        edition=edition,
        stories=stories,
        articles=articles,
        publish_result=publish_result,
        provider=getattr(provider, "name", type(provider).__name__),
        provider_note=provider_note,
        review_count=len(review_articles) + len(draft_errors),
        flags=flags,
        stats={
            "stories": len(stories),
            "articles": len(articles),
            "needs_review": len(review_articles),
            "open_review_flags": len(flag_rows),
            "cleared_drafts": cleared,
            "draft_errors": draft_errors,
            "window_start": request.window_start.isoformat(),
            "window_end": request.window_end.isoformat(),
        },
    )
    result.duration_seconds = round(time.monotonic() - started, 2)
    _notify(request, "publishing", f"Edition {edition.id} is {edition.status}")
    return result


def run_manual_edition(
    db: Session,
    channel: Channel,
    *,
    window_start: datetime,
    window_end: datetime,
    cutoff_at: datetime | None = None,
    target_release_at: datetime | None = None,
    actor_id: int | None = None,
    provider: DraftProvider | None = None,
    mark_published: bool = True,
    progress: ProgressCallback | None = None,
) -> PipelineResult:
    now = utcnow()
    return run_pipeline(
        db,
        PipelineRequest(
            channel=channel,
            window_start=window_start,
            window_end=window_end,
            cutoff_at=cutoff_at or window_end,
            target_release_at=target_release_at or now,
            kind="manual",
            actor_id=actor_id,
            provider=provider,
            mark_published=mark_published,
            progress=progress,
            source_label="manual",
        ),
    )
