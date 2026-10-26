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
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .ai.providers import (
    DraftProvider,
    FixtureDraftProvider,
    OllamaDraftProvider,
)
from .auth import active_rights
from .config import settings
from .editorial.clustering import build_stories, clear_editorial_drafts
from .editorial.drafting import draft_story
from .models import (
    Article,
    ArticleSource,
    Channel,
    Edition,
    EditionArticle,
    Job,
    PostFragment,
    PublicationProfile,
    ReviewFlag,
    StoryCluster,
)
from .publications import (
    default_publication,
    serialise_channel_ids,
    source_priority_map,
)
from .publishing.service import PublishResult, publish_edition
from .media.service import prepare_edition_media
from .ingestion.service import reclassify_posts
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
    # Multi-source support. ``channels`` is the authoritative list when set;
    # ``channel`` stays the primary channel for legacy keys and paths.
    channels: list[Channel] | None = None
    publication: PublicationProfile | None = None


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


def next_issue_number_for_publication(
    db: Session, publication: PublicationProfile, kind: str
) -> int:
    """Issue numbers belong to the newspaper, not to one source channel."""
    value = db.scalar(
        select(func.max(Edition.issue_number)).where(
            Edition.publication_id == publication.id, Edition.kind == kind
        )
    )
    return int(value or 0) + 1


def next_volume(db: Session, channel_id: int) -> int:
    profile = db.get(PublicationProfile, 1) or db.scalar(select(PublicationProfile).limit(1))
    return int(profile.issue_volume if profile else 1)


def select_provider(
    channel: Channel | list[Channel], requested: DraftProvider | None = None
) -> tuple[DraftProvider, str]:
    channels = channel if isinstance(channel, list) else [channel]
    if requested is not None:
        return requested, ""
    if channels and all(item.is_demo for item in channels):
        return FixtureDraftProvider(), "fixture deterministic composer"
    candidate = OllamaDraftProvider()
    available, message = candidate.available()
    if available:
        return candidate, f"ollama {candidate.model}"
    raise PipelineError(
        f"Real channel drafting cannot continue because Ollama is unavailable: {message}. "
        "Fixture drafting is disabled for non-demo channels."
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


_STORY_STOPWORDS = {
    "the", "and", "for", "with", "from", "after", "before", "over", "into",
    "that", "this", "says", "said", "amid", "will", "have", "has", "were",
    "was", "are", "its", "his", "her", "their", "they", "them", "been",
    "more", "than", "about", "against", "across", "under", "near", "new",
}


def _story_tokens(value: str) -> set[str]:
    import re

    return {
        token
        for token in re.findall(r"[a-z0-9]+", (value or "").casefold())
        if len(token) > 2 and token not in _STORY_STOPWORDS
    }


def _same_story_title(left: str, right: str) -> bool:
    """Conservative duplicate test for two channels reporting one event.

    Requires at least three shared significant words and either strong
    containment or a close whole-title match. A shared dateline alone never
    merges two different incidents.
    """
    from difflib import SequenceMatcher

    a = _story_tokens(left)
    b = _story_tokens(right)
    if len(a & b) < 3:
        return False
    containment = len(a & b) / min(len(a), len(b))
    ratio = SequenceMatcher(None, " ".join(sorted(a)), " ".join(sorted(b))).ratio()
    return containment >= 0.62 or ratio >= 0.74


def _absorb_story(db: Session, primary: StoryCluster, duplicate: StoryCluster) -> None:
    """Move evidence from a duplicate story onto the kept story, keeping history."""
    from sqlalchemy import delete, update

    from .models import ExternalReference, StoryClaim, StoryRelationship, StoryTimelineEvent

    db.execute(
        update(PostFragment)
        .where(PostFragment.story_id == duplicate.id)
        .values(story_id=primary.id)
    )
    existing_claims = {
        (claim.text or "").strip().casefold()
        for claim in db.scalars(select(StoryClaim).where(StoryClaim.story_id == primary.id))
    }
    for claim in db.scalars(select(StoryClaim).where(StoryClaim.story_id == duplicate.id)):
        key = (claim.text or "").strip().casefold()
        if key and key in existing_claims:
            db.execute(delete(StoryClaim).where(StoryClaim.id == claim.id))
            continue
        claim.story_id = primary.id
        existing_claims.add(key)
    existing_urls = {
        (ref.url or "").strip()
        for ref in db.scalars(
            select(ExternalReference).where(ExternalReference.story_id == primary.id)
        )
    }
    for ref in db.scalars(
        select(ExternalReference).where(ExternalReference.story_id == duplicate.id)
    ):
        if ref.url and ref.url.strip() in existing_urls:
            db.execute(delete(ExternalReference).where(ExternalReference.id == ref.id))
            continue
        ref.story_id = primary.id
        existing_urls.add((ref.url or "").strip())
    db.execute(
        update(StoryTimelineEvent)
        .where(StoryTimelineEvent.story_id == duplicate.id)
        .values(story_id=primary.id)
    )
    db.execute(
        update(StoryRelationship)
        .where(StoryRelationship.story_id == duplicate.id)
        .values(story_id=primary.id)
    )
    db.execute(
        update(StoryRelationship)
        .where(StoryRelationship.related_story_id == duplicate.id)
        .values(related_story_id=primary.id)
    )
    db.execute(
        update(ReviewFlag)
        .where(ReviewFlag.story_id == duplicate.id)
        .values(story_id=primary.id)
    )
    # The secondary row stays as an audit pointer rather than disappearing, so a
    # later investigation can see which channel's version was folded in.
    duplicate.merged_into_id = primary.id
    duplicate.status = "merged"
    primary.importance = max(primary.importance, duplicate.importance)
    if len((duplicate.summary or "")) > len((primary.summary or "")):
        primary.summary = duplicate.summary


def merge_cross_source_stories(
    db: Session,
    stories: list[StoryCluster],
    *,
    priorities: dict[int, tuple[int, float]] | None = None,
) -> tuple[list[StoryCluster], dict[int, list[StoryCluster]]]:
    """Fold duplicate reports from different channels into one story."""
    priorities = priorities or {}

    def rank(story: StoryCluster) -> tuple[float, int]:
        weight = priorities.get(story.channel_id, (100, 1.0))[1]
        return (-(story.importance * max(weight, 0.1)), story.id)

    kept: list[StoryCluster] = []
    absorbed: dict[int, list[StoryCluster]] = {}
    for story in sorted(stories, key=rank):
        if story.merged_into_id:
            continue
        primary = next(
            (
                candidate
                for candidate in kept
                if candidate.section == story.section
                and _same_story_title(candidate.canonical_title, story.canonical_title)
            ),
            None,
        )
        if primary is None:
            kept.append(story)
            continue
        _absorb_story(db, primary, story)
        absorbed.setdefault(primary.id, []).append(story)
    db.flush()
    return kept, absorbed


def record_article_sources(db: Session, article: Article) -> None:
    """Persist which channels and posts an article actually draws on."""
    from sqlalchemy import delete

    from .models import SourcePost

    rows = db.execute(
        select(
            SourcePost.channel_id,
            func.count(func.distinct(SourcePost.id)),
            func.min(SourcePost.posted_at),
        )
        .join(PostFragment, PostFragment.post_id == SourcePost.id)
        .where(PostFragment.story_id == article.story_id)
        .group_by(SourcePost.channel_id)
    ).all()
    db.execute(delete(ArticleSource).where(ArticleSource.article_id == article.id))
    for channel_id, count, first_posted in rows:
        db.add(
            ArticleSource(
                article_id=article.id,
                channel_id=channel_id,
                post_count=int(count or 0),
                first_posted_at=first_posted,
            )
        )
    db.flush()


def run_pipeline(db: Session, request: PipelineRequest) -> PipelineResult:
    started = time.monotonic()
    channel = request.channel
    publication = request.publication or default_publication(db)
    if request.window_start >= request.window_end:
        raise PipelineError("Edition window start must be before window end.")
    # One newspaper can draw on several authorized channels. The explicit list
    # wins; otherwise the legacy single channel is used so existing callers are
    # unchanged.
    channels = list(request.channels or [channel])
    seen_channels: set[int] = set()
    channels = [item for item in channels if not (item.id in seen_channels or seen_channels.add(item.id))]
    if not channels:
        raise PipelineError("An edition needs at least one enabled source channel.")
    primary = channels[0]
    for source_channel in channels:
        if not source_channel.is_demo and active_rights(db, source_channel.id) is None:
            raise RightsError(
                f"{source_channel.title} has no active content-rights declaration. "
                "Importing and AI processing for this newspaper are blocked until an owner "
                "records a grant."
            )

    if publication is None:
        raise PipelineError("Publication profile is missing; run setup first.")

    issue_number = request.issue_number or next_issue_number_for_publication(
        db, publication, request.kind
    )
    volume = request.volume or int(publication.issue_volume or 1)
    title = request.title.strip() or f"{publication.name} — {request.kind.replace('_', ' ').title()}"
    job = db.get(Job, request.job_id) if request.job_id else None
    resumed = db.get(Edition, job.edition_id) if job and job.edition_id else None
    source_ids_json = serialise_channel_ids(channels)
    edition = resumed or Edition(
        channel_id=primary.id,
        publication_id=publication.id,
        source_channel_ids_json=source_ids_json,
        source_status="complete",
        kind=request.kind,
        title=title[:240],
        volume=volume,
        issue_number=issue_number,
        status="draft",
        window_start=request.window_start,
        window_end=request.window_end,
        cutoff_at=request.cutoff_at,
        target_release_at=request.target_release_at,
        timezone=publication.timezone,
        version=1,
        profile_snapshot_json=json.dumps(
            {
                "name": publication.name,
                "handle": publication.channel_handle,
                "city": publication.city,
                "timezone": publication.timezone,
                "accent": publication.accent,
                "paper_size": publication.paper_size,
                "map_profile": publication.map_profile,
                "qr_strategy": publication.qr_strategy,
            },
            ensure_ascii=False,
        ),
        qr_policy=publication.qr_strategy,
        created_by=request.actor_id,
    )
    if resumed:
        edition.channel_id = primary.id
        edition.publication_id = publication.id
        edition.source_channel_ids_json = source_ids_json
    db.add(edition)
    db.flush()

    cleared = 0
    reclassification = None
    reclassification_totals = {
        "scanned": 0,
        "reclassified": 0,
        "included": 0,
        "excluded": 0,
        "overrides": 0,
        "duplicates": 0,
        "fragments_created": 0,
        "by_classification": {},
    }
    story_errors: list[str] = []
    if resumed:
        ids = json.loads(job.result_json or "{}").get("story_ids", [])
        stories = list(db.scalars(select(StoryCluster).where(StoryCluster.id.in_(ids))))
    else:
        collected_stories: list[StoryCluster] = []
        for source_index, source_channel in enumerate(channels, start=1):
            # Stored posts imported before the current ruleset would otherwise
            # bypass new exclusion rules because clustering only reads
            # inclusion_status. Reclassify every source window before any draft
            # is cleared or built; explicit editor overrides are preserved.
            _notify(
                request,
                "classification",
                f"Source {source_index}/{len(channels)}: re-checking {source_channel.title}",
            )
            channel_reclassification = reclassify_posts(
                db,
                source_channel,
                start=request.window_start,
                end=request.window_end,
                actor_id=request.actor_id,
            )
            reclassification = channel_reclassification
            for key in ("scanned", "reclassified", "included", "excluded", "overrides", "duplicates", "fragments_created"):
                reclassification_totals[key] += int(getattr(channel_reclassification, key, 0) or 0)
            for name, count in (channel_reclassification.by_classification or {}).items():
                reclassification_totals["by_classification"][name] = (
                    reclassification_totals["by_classification"].get(name, 0) + count
                )
            if channel_reclassification.fragments_created == 0 and channel_reclassification.included == 0:
                story_errors.append(f"{source_channel.title}: no includable posts in this window")
            _notify(
                request,
                "classification",
                f"{source_channel.title}: {channel_reclassification.reclassified} checked, "
                f"{channel_reclassification.included} included, "
                f"{channel_reclassification.excluded} excluded",
            )
            _notify(request, "normalization", f"Clearing unpublished drafts for {source_channel.title}")
            cleared += clear_editorial_drafts(
                db, source_channel.id, request.window_start, request.window_end
            )
            _notify(request, "clustering", f"Building story clusters from {source_channel.title}")
            collected_stories.extend(
                build_stories(
                    db,
                    source_channel.id,
                    request.window_start,
                    request.window_end,
                    job_id=request.job_id,
                )
            )
        _notify(request, "clustering", "Checking cross-source duplicates")
        stories, absorbed = merge_cross_source_stories(
            db,
            collected_stories,
            priorities=source_priority_map(db, publication),
        )
        if absorbed:
            _notify(
                request,
                "clustering",
                f"Merged {sum(len(items) for items in absorbed.values())} duplicate cross-source reports",
            )
    if not stories:
        detail = "; ".join(story_errors[:3]) if story_errors else ""
        raise PipelineError(
            "No includable source fragments were found in this window. Import authorized posts "
            "or widen the reporting window." + (f" {detail}" if detail else "")
        )

    if job:
        job.edition_id = edition.id
        job.result_json = json.dumps({"story_ids": [story.id for story in stories]})
        db.commit()
    provider, provider_note = select_provider(channels, request.provider)
    _notify(request, "drafting", f"Drafting {len(stories)} stories with {getattr(provider, 'name', 'provider')}")
    articles: list[Article] = []
    draft_errors: list[str] = []
    try:
        for index, story in enumerate(stories, start=1):
            if job:
                from .operations import check_job_control
                check_job_control(job.id)
            try:
                _notify(request, "drafting", f"Drafting story {index}/{len(stories)}")
                article = draft_story(
                    db,
                    story,
                    provider,
                    job_id=request.job_id,
                    actor_id=request.actor_id,
                )
                record_article_sources(db, article)
                articles.append(article)
                if job:
                    db.commit()
            except Exception as exc:  # one bad story must not lose a valid edition
                draft_errors.append(f"story {story.id}: {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        # Free the VRAM as soon as drafting is done instead of leaving a local
        # model resident between scheduled runs on a 6 GB laptop GPU.
        if settings.ollama_unload_after_job:
            unload = getattr(provider, "unload", None)
            if callable(unload):
                unload()

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
    if resumed:
        db.execute(EditionArticle.__table__.delete().where(EditionArticle.edition_id == edition.id))
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

    _notify(request, "media_prepare", f"Preparing media for {len(stories)} stories")
    media_stats = prepare_edition_media(
        db, edition.id, stories, job_id=request.job_id, progress=request.progress
    )
    _notify(
        request,
        "media_download",
        f"Media: {media_stats['discovered']} discovered, {media_stats['selected']} selected, "
        f"{media_stats['downloaded']} downloaded ({media_stats['bytes_downloaded']} bytes), "
        f"{media_stats['skipped_cached']} already cached, {media_stats['skipped_policy']} skipped by policy, "
        f"{media_stats['failed']} failed",
    )
    _notify(request, "media_process", "Media preparation complete; layout will use local files only")
    _notify(request, "layout", "Composing tabloid pages and rendering the PDF")
    if job:
        db.commit()
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
            layout_progress=request.progress,
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
            "reclassification": (
                dict(reclassification_totals, channels=len(channels))
                if reclassification
                else None
            ),
            "draft_errors": draft_errors,
            "source_errors": story_errors,
            "sources": [
                {"id": item.id, "title": item.title, "handle": item.handle}
                for item in channels
            ],
            "media": media_stats,
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
