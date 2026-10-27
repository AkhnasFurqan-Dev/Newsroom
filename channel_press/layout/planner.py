from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import SECTION_ORDER
from ..graphics.cartography import MapResult, render_locator_map
from ..graphics.charts import ChartSpec, build_percentage_chart, render_chart_svg, render_timeline_html
from ..media.service import select_lead_media
from ..models import (
    Article,
    ArticleMedia,
    CartographicProfile,
    Channel,
    Edition,
    EditionArticle,
    ExternalReference,
    PostFragment,
    PublicationProfile,
    SourcePost,
    StoryClaim,
    StoryCluster,
    StoryTimelineEvent,
)
from ..timeutil import iso_utc
from ..editorial.entities import sentence_split


@dataclass
class StoryPackage:
    article: Article
    story: StoryCluster
    section: str
    genre: str
    is_front: bool = False
    is_brief: bool = False
    headline_size: str = "section"
    media_id: int | None = None
    media_path: str = ""
    media_caption: str = ""
    media_credit: str = ""
    media_label: str = ""
    supporting_media_id: int | None = None
    supporting_media_path: str = ""
    supporting_media_caption: str = ""
    supporting_media_credit: str = ""
    supporting_media_label: str = ""
    map_result: MapResult | None = None
    chart_svg: str = ""
    chart_spec: ChartSpec | None = None
    timeline_html: str = ""
    sources: list[dict] = field(default_factory=list)
    source_count: int = 0
    correction_note: str = ""
    teaser: str = ""
    teaser_body: list[str] = field(default_factory=list)
    body_paragraphs: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)


@dataclass
class EditionPlan:
    lead: StoryPackage
    front_briefs: list[StoryPackage]
    sections: list[tuple[str, list[StoryPackage]]]
    packages: list[StoryPackage]
    profile: PublicationProfile
    appendix_entries: list[dict]

    @property
    def page_templates(self) -> list[str]:
        names = ["front_major_event" if self.lead.headline_size == "major" else "front_standard"]
        for section, _ in self.sections:
            names.append(
                {
                    "conflict": "conflict_visual",
                    "analysis": "analysis_feature",
                    "opinion": "opinion",
                    "briefs": "briefs",
                    "national": "national",
                    "regional": "regional",
                    "international": "international",
                    "defence": "defence",
                }.get(section, "national")
            )
        names.append("sources")
        return names


def _body(article: Article) -> list[str]:
    try:
        value = json.loads(article.body_json or "[]")
        return [str(p) for p in value if str(p).strip()]
    except json.JSONDecodeError:
        return [p for p in article.body_text.split("\n\n") if p.strip()]


def _first_sentence(article: Article, limit: int = 220) -> str:
    sentences = sentence_split(article.body_text)
    value = sentences[0] if sentences else article.title
    if len(value) > limit:
        value = value[: limit - 1].rsplit(" ", 1)[0] + "…"
    return value


def _channel_label(db: Session, channel_id: int) -> str:
    channel = db.get(Channel, channel_id)
    if channel is None:
        return ""
    return (channel.handle or channel.title or "").strip()


def _source_entries(db: Session, article: Article) -> list[dict]:
    rows = db.execute(
        select(PostFragment, SourcePost)
        .join(SourcePost, PostFragment.post_id == SourcePost.id)
        .where(PostFragment.story_id == article.story_id)
        .order_by(SourcePost.posted_at.asc())
    ).all()
    seen: dict[int, dict] = {}
    for fragment, post in rows:
        if post.id in seen:
            continue
        seen[post.id] = {
            "post_id": post.id,
            "message_id": post.telegram_message_id,
            "posted_at": iso_utc(post.posted_at),
            "date_label": post.posted_at.strftime("%d %b %Y, %H:%M UTC"),
            "source_url": post.source_url,
            "language": post.language,
            "text": post.text[:240],
            "channel_id": post.channel_id,
            "channel": _channel_label(db, post.channel_id),
        }
    return list(seen.values())


def _build_package(
    db: Session,
    article: Article,
    *,
    section: str,
    is_front: bool = False,
    is_brief: bool = False,
    profile: PublicationProfile,
    map_profile: CartographicProfile | None,
    media_dir: Path | None = None,
    exclude_media_ids: set[int] | None = None,
    download_media: bool = True,
) -> StoryPackage:
    story = db.get(StoryCluster, article.story_id)
    paragraphs = _body(article)
    package = StoryPackage(
        article=article,
        story=story,
        section=section,
        genre=article.genre,
        is_front=is_front,
        is_brief=is_brief,
        body_paragraphs=paragraphs,
        teaser_body=paragraphs[:2],
        teaser=_first_sentence(article, limit=190),
        source_count=len(_source_entries(db, article)),
        sources=_source_entries(db, article),
    )
    if is_front:
        package.headline_size = "major" if story.importance >= 0.78 or story.risk_level == "high" else "front"
    elif is_brief:
        package.headline_size = "brief"
    else:
        package.headline_size = "section"

    if media_dir is not None:

        def _current(role: str) -> ArticleMedia | None:
            row = db.scalar(
                select(ArticleMedia)
                .where(
                    ArticleMedia.article_id == article.id,
                    ArticleMedia.role == role,
                    ArticleMedia.selected.is_(True),
                )
                .order_by(ArticleMedia.id.desc())
                .limit(1)
            )
            if row is not None and (not row.path or not Path(row.path).is_file()):
                # A cleared cache or removed output folder must never leave the
                # template pointing at a missing image. Drop the stale
                # selection and let the caller rebuild from local media.
                db.delete(row)
                db.flush()
                return None
            return row

        existing = _current("lead")
        if existing is None:
            choice = select_lead_media(
                db,
                story,
                article.id,
                media_dir,
                exclude_media_ids=exclude_media_ids,
                download_missing=download_media,
            )
            if choice:
                existing = _current("lead")
        if existing is not None and existing.media_id is not None and existing.media_id in (exclude_media_ids or set()):
            package.media_id = None
            package.media_path = ""
        elif existing is not None:
            package.media_id = existing.media_id
            package.media_path = existing.path
            package.media_caption = existing.caption
            package.media_credit = existing.credit
            package.media_label = "VIDEO STILL" if existing.graphic_kind == "video_still" else (
                "FIXTURE TEST PLATE" if not (existing.caption or "").lower().startswith("source") and "fixture" in (existing.caption or "").lower() else ""
            )
        # A supporting asset is an explicit editor choice and is never
        # auto-selected. Briefs only ever carry one picture so a two-image
        # block cannot overflow a narrow brief column.
        supporting = None if is_brief else _current("supporting")
        if supporting is not None and supporting.media_id is not None and supporting.media_id in (exclude_media_ids or set()):
            supporting = None
        if supporting is not None:
            package.supporting_media_id = supporting.media_id
            package.supporting_media_path = supporting.path
            package.supporting_media_caption = supporting.caption
            package.supporting_media_credit = supporting.credit
            package.supporting_media_label = "VIDEO STILL" if supporting.graphic_kind == "video_still" else ""
    if not package.media_path and map_profile is not None:
        map_result = render_locator_map(story, map_profile)
        if not map_result.fallback:
            package.map_result = map_result
    if article.section in {"analysis", "opinion"}:
        package.map_result = None
    claims = list(db.scalars(select(StoryClaim).where(StoryClaim.story_id == story.id)))
    if section in {"national", "analysis"} and not package.chart_svg:
        spec = build_percentage_chart(claims)
        if spec is not None:
            package.chart_spec = spec
            package.chart_svg = render_chart_svg(spec)
    if is_front or (section == "conflict" and not is_brief):
        events = list(
            db.scalars(
                select(StoryTimelineEvent)
                .where(StoryTimelineEvent.story_id == story.id)
                .order_by(StoryTimelineEvent.event_at.asc())
            )
        )
        if len(events) >= 2:
            # The front page has a fixed height and carries the lead image/map.
            # A long chronology was the one unbounded element that could push
            # the lead past the page; cap it and state the omitted count.
            limit = 6 if is_front else 8
            package.timeline_html = render_timeline_html(
                events, profile.timezone, max_events=limit
            )
    if any(claim.verification_status == "superseded" for claim in claims) or any(
        claim.claim_type == "correction" for claim in claims
    ):
        package.correction_note = (
            "The channel published a correction to an earlier report. This article uses the corrected figure "
            "and records the change in its source appendix."
        )
    return package


def create_plan(
    db: Session,
    edition: Edition,
    profile: PublicationProfile,
    *,
    media_dir: Path | None = None,
    download_media: bool = False,
) -> EditionPlan:
    links = list(
        db.scalars(
            select(EditionArticle)
            .where(EditionArticle.edition_id == edition.id)
            .order_by(EditionArticle.sort_order.asc())
        )
    )
    if not links:
        articles = list(
            db.scalars(
                select(Article)
                .join(StoryCluster, Article.story_id == StoryCluster.id)
                .where(Article.status != "rejected")
                .order_by(StoryCluster.importance.desc(), Article.id.asc())
            )
        )
        # Only include stories whose fragments fall inside this edition window.
        eligible_ids = set(
            db.scalars(
                select(PostFragment.story_id)
                .join(SourcePost, PostFragment.post_id == SourcePost.id)
                .where(
                    SourcePost.channel_id == edition.channel_id,
                    SourcePost.posted_at >= edition.window_start,
                    SourcePost.posted_at < edition.window_end,
                    SourcePost.inclusion_status == "included",
                )
            )
        )
        articles = [article for article in articles if article.story_id in eligible_ids]
        for index, article in enumerate(articles):
            links.append(
                EditionArticle(
                    edition_id=edition.id,
                    article_id=article.id,
                    section=article.section,
                    sort_order=index,
                    is_front=False,
                )
            )
            db.add(links[-1])
        db.flush()
    if not links:
        raise ValueError("No eligible articles were found for this edition window.")
    map_profile = db.scalar(
        select(CartographicProfile).where(CartographicProfile.key == profile.map_profile)
    ) or db.scalar(select(CartographicProfile).limit(1))
    packages: list[StoryPackage] = []
    used_media_ids: set[int] = set()
    from ..editorial.revisions import EXCLUDED_STATUSES

    for link in links:
        article = db.get(Article, link.article_id)
        if article is None:
            continue
        # Editor decisions are authoritative for the printed product: an
        # excluded article is never composed into the paper, and a held article
        # stays out until the hold is lifted. Release is blocked separately so
        # a hold cannot silently disappear.
        if article.status in EXCLUDED_STATUSES or article.status == "held":
            continue
        package = _build_package(
            db,
            article,
            section=link.section,
            is_front=link.is_front,
            profile=profile,
            map_profile=map_profile,
            media_dir=media_dir,
            download_media=download_media,
            exclude_media_ids=used_media_ids,
        )
        if package.media_id is not None:
            used_media_ids.add(package.media_id)
        packages.append(package)
    if not packages:
        raise ValueError(
            "Every article in this edition is excluded or held; restore at least one article before rendering."
        )
    news = [p for p in packages if p.genre == "news"]
    lead = max(news, key=lambda p: p.story.importance, default=packages[0])
    # Never repeat the same source still for two different reports in one edition.
    used_media: set[int] = set()
    for package in packages:
        if package.media_id is None:
            continue
        if package.media_id in used_media:
            package.media_id = None
            package.media_path = ""
            package.media_caption = ""
            package.media_credit = ""
            package.media_label = ""
            if map_profile is not None and package.map_result is None:
                map_result = render_locator_map(package.story, map_profile)
                if not map_result.fallback:
                    package.map_result = map_result
        else:
            used_media.add(package.media_id)
    lead.is_front = True
    lead.headline_size = "major" if lead.story.importance >= 0.72 or lead.story.risk_level == "high" else "front"
    # Rebuild front lead with visual hierarchy; map preferred over a fixture plate.
    if map_profile is not None:
        map_result = render_locator_map(lead.story, map_profile)
        if not map_result.fallback:
            lead.map_result = map_result
    for link in links:
        if link.article_id == lead.article.id:
            link.is_front = True
            link.section = lead.article.section
    # Briefs: short national/regional/international items move to the Briefs page;
    # short conflict/defence items remain with their section.
    for package in packages:
        if package is lead:
            continue
        if (
            package.article.word_count < 50
            and package.section in {"national", "regional", "international"}
            and package.genre == "news"
        ):
            package.section = "briefs"
            package.is_brief = True
            package.headline_size = "brief"
            for link in links:
                if link.article_id == package.article.id:
                    link.section = "briefs"
    # Front briefs exclude section briefs to avoid duplication.
    front_briefs = [
        p for p in sorted(packages, key=lambda p: p.story.importance, reverse=True)
        if p is not lead and p.section != "briefs" and p.genre == "news"
    ][:4]
    section_map: dict[str, list[StoryPackage]] = {}
    for package in packages:
        section_map.setdefault(package.section, []).append(package)
    ordered_sections: list[tuple[str, list[StoryPackage]]] = []
    order = SECTION_ORDER.copy()
    try:
        custom = json.loads(profile.section_order_json or "[]")
        if isinstance(custom, list) and custom:
            order = [s for s in custom if s in SECTION_ORDER] + [s for s in SECTION_ORDER if s not in custom]
    except json.JSONDecodeError:
        pass
    for section in order:
        items = section_map.get(section)
        if not items:
            continue
        items.sort(key=lambda p: (p is not lead, -p.story.importance))
        ordered_sections.append((section, items))
    appendix_entries: list[dict] = []
    for index, package in enumerate(packages, start=1):
        for source in package.sources:
            appendix_entries.append(
                {
                    "ref": "",
                    "article_id": package.article.id,
                    "article": package.article.title,
                    "message_id": source["message_id"],
                    "date": source["date_label"],
                    "url": source["source_url"],
                    "language": source["language"],
                    "channel": source.get("channel", ""),
                    "channel_id": source.get("channel_id"),
                    "kind": "fixture" if not source["source_url"] else "telegram",
                }
            )
    for package in packages:
        for ref in db.scalars(
            select(ExternalReference).where(
                ExternalReference.story_id == package.story.id,
                ExternalReference.review_status == "accepted",
            )
        ):
            appendix_entries.append(
                {
                    "ref": "",
                    "article_id": package.article.id,
                    "article": package.article.title,
                    "message_id": None,
                    "date": ref.accessed_at.strftime("%d %b %Y"),
                    "url": ref.url,
                    "language": "",
                    "kind": "external_link_unverified",
                    "title": ref.title or "",
                    "publisher": ref.publisher or "",
                }
            )
    return EditionPlan(
        lead=lead,
        front_briefs=front_briefs,
        sections=ordered_sections,
        packages=packages,
        profile=profile,
        appendix_entries=appendix_entries,
    )
