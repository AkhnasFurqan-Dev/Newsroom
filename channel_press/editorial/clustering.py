from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..constants import RISK_TERMS
from ..models import (
    Article,
    ArticleClaimLink,
    ArticleMedia,
    ArticleVersion,
    EditorialOverride,
    PostFragment,
    ReplyEdge,
    ReviewFlag,
    SourcePost,
    StoryClaim,
    StoryCluster,
    StoryRelationship,
    StoryTimelineEvent,
)
from ..timeutil import utcnow
from .entities import (
    headline_from_text,
    infer_section,
    jaccard,
    locations,
    proper_nouns,
    token_set,
)


@dataclass
class FragmentView:
    fragment: PostFragment
    post: SourcePost
    text: str
    title: str
    incident_key: str
    location: str
    topic: str
    genre: str
    tokens: set[str]
    nouns: set[str]
    places: set[str]


def _meta(fragment: PostFragment) -> dict:
    try:
        value = json.loads(fragment.topics_json or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _seed_title(fragment: PostFragment) -> str:
    meta = _meta(fragment)
    seed = meta.get("article_seed")
    if isinstance(seed, dict):
        return str(seed.get("title") or "").strip()
    return ""


def load_views(db: Session, channel_id: int, start: datetime, end: datetime) -> list[FragmentView]:
    rows = db.execute(
        select(PostFragment, SourcePost)
        .join(SourcePost, PostFragment.post_id == SourcePost.id)
        .where(
            SourcePost.channel_id == channel_id,
            SourcePost.posted_at >= start,
            SourcePost.posted_at < end,
            SourcePost.inclusion_status == "included",
            SourcePost.deleted_at.is_(None),
        )
        .order_by(SourcePost.posted_at.asc(), PostFragment.sequence.asc())
    ).all()
    views: list[FragmentView] = []
    for fragment, post in rows:
        text = fragment.text.strip()
        if not text:
            continue
        meta = _meta(fragment)
        topics = meta.get("topics") or []
        topic = topics[0] if topics else ""
        views.append(
            FragmentView(
                fragment=fragment,
                post=post,
                text=text,
                title=str(meta.get("title") or ""),
                incident_key=str(meta.get("incident_key") or ""),
                location=str(meta.get("location") or ""),
                topic=str(topic),
                genre=str(meta.get("genre") or "news"),
                tokens=token_set(text),
                nouns=proper_nouns(text),
                places=locations(text),
            )
        )
    return views


def clear_editorial_drafts(db: Session, channel_id: int, start: datetime, end: datetime) -> int:
    """Remove unpublished drafts in a window so a retry is idempotent.

    Published editions are immutable and keep their own article snapshots, so
    their articles are never deleted. Locked drafts are preserved.
    """
    from ..models import EditionArticle

    story_ids = set(
        db.scalars(
            select(PostFragment.story_id).where(
                PostFragment.story_id.is_not(None),
                PostFragment.post_id.in_(
                    select(SourcePost.id).where(
                        SourcePost.channel_id == channel_id,
                        SourcePost.posted_at >= start,
                        SourcePost.posted_at < end,
                    )
                ),
            )
        )
    )
    cleared = 0
    for story_id in story_ids:
        if story_id is None:
            continue
        article = db.scalar(select(Article).where(Article.story_id == story_id).limit(1))
        if article is not None:
            in_edition = db.scalar(select(EditionArticle.id).where(EditionArticle.article_id == article.id).limit(1))
            if article.locked or in_edition:
                continue
            db.execute(delete(ArticleClaimLink).where(ArticleClaimLink.article_id == article.id))
            db.execute(delete(ArticleMedia).where(ArticleMedia.article_id == article.id))
            db.execute(delete(ArticleVersion).where(ArticleVersion.article_id == article.id))
            db.execute(delete(EditorialOverride).where(EditorialOverride.article_id == article.id))
            db.execute(delete(ReviewFlag).where(ReviewFlag.article_id == article.id))
            db.delete(article)
        if db.scalar(select(EditionArticle.id).where(EditionArticle.article_id.in_(select(Article.id).where(Article.story_id == story_id))).limit(1)):
            continue
        db.execute(delete(StoryClaim).where(StoryClaim.story_id == story_id))
        db.execute(delete(StoryTimelineEvent).where(StoryTimelineEvent.story_id == story_id))
        db.execute(delete(StoryRelationship).where(StoryRelationship.story_id == story_id))
        db.execute(delete(StoryRelationship).where(StoryRelationship.related_story_id == story_id))
        db.execute(delete(ReviewFlag).where(ReviewFlag.story_id == story_id))
        db.execute(PostFragment.__table__.update().where(PostFragment.story_id == story_id).values(story_id=None))
        story = db.get(StoryCluster, story_id)
        if story:
            db.delete(story)
        cleared += 1
    db.flush()
    return cleared


def _fingerprint(view: FragmentView) -> str:
    if view.incident_key:
        basis = f"incident:{view.incident_key}"
    elif view.location:
        basis = f"place:{view.location}:{' '.join(sorted(list(view.tokens))[:8])}"
    elif view.title:
        basis = f"title:{view.title.lower()}"
    else:
        basis = f"text:{' '.join(sorted(list(view.tokens))[:10])}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _similarity(view: FragmentView, cluster_views: list[FragmentView]) -> tuple[float, bool]:
    if not cluster_views:
        return 0.0, False
    best = 0.0
    shared_place = False
    for other in cluster_views:
        if view.incident_key and view.incident_key == other.incident_key:
            return 1.0, True
        overlap = jaccard(view.tokens, other.tokens)
        place = bool(view.places & other.places) or (view.location and view.location in other.location)
        shared_nouns = view.nouns & other.nouns
        time_gap = abs((view.post.posted_at - other.post.posted_at).total_seconds())
        if place and overlap >= 0.20 and time_gap <= 72 * 3600:
            best = max(best, overlap + 0.12)
            shared_place = True
        elif overlap >= 0.42 and shared_nouns and time_gap <= 48 * 3600:
            best = max(best, overlap)
            shared_place = True
    return best, shared_place


def _section_and_genre(view: FragmentView, all_views: list[FragmentView], reply_target: StoryCluster | None) -> tuple[str, str]:
    genre = view.genre if view.genre in {"news", "analysis", "opinion", "editor_note", "brief"} else "news"
    if genre in {"analysis", "opinion"}:
        return ("analysis" if genre == "analysis" else "opinion"), genre
    text = " ".join(v.text for v in all_views)
    return infer_section(text, view.topic), "news"


def _importance(section: str, views: list[FragmentView], genre: str) -> float:
    base = {
        "conflict": 0.58,
        "defence": 0.50,
        "international": 0.47,
        "national": 0.48,
        "regional": 0.38,
        "analysis": 0.33,
        "opinion": 0.28,
        "briefs": 0.22,
    }.get(section, 0.35)
    text = " ".join(v.text.lower() for v in views)
    severity = 0.0
    for term in ("killed", "wounded", "dead", "attack", "bomb", "clearance", "border", "closure", "ceasefire"):
        if term in text:
            severity += 0.025
    severity = min(severity, 0.16)
    source_bonus = min(len({v.post.id for v in views}) * 0.025, 0.18)
    recency = 0.0
    latest = max(v.post.posted_at for v in views)
    hours_old = (utcnow() - latest).total_seconds() / 3600
    if hours_old < 6:
        recency = 0.05
    elif hours_old < 24:
        recency = 0.03
    genre_penalty = 0.04 if genre in {"analysis", "opinion"} else 0.0
    return round(min(base + severity + source_bonus + recency - genre_penalty, 0.99), 4)


def _risk(text: str) -> tuple[str, list[str]]:
    tags = []
    lower = text.lower()
    casualty_text = re.sub(
        r"\b(no|without)\s+(reported\s+)?(casualties|injuries|deaths|fatalities)\b", "", lower
    )
    casualty_text = re.sub(r"\bno one (was )?(killed|injured|hurt)\b", "", casualty_text)
    for tag, terms in RISK_TERMS.items():
        haystack = casualty_text if tag == "casualties" else lower
        if any(term in haystack for term in terms):
            tags.append(tag)
    if tags:
        return "high", tags
    return "low", tags


def build_stories(
    db: Session,
    channel_id: int,
    start: datetime,
    end: datetime,
    *,
    job_id: int | None = None,
) -> list[StoryCluster]:
    views = load_views(db, channel_id, start, end)
    by_post: dict[int, list[FragmentView]] = {}
    for view in views:
        by_post.setdefault(view.post.id, []).append(view)

    clusters: list[StoryCluster] = []
    cluster_views: dict[int, list[FragmentView]] = {}

    for view in views:
        target_cluster: StoryCluster | None = None
        if view.post.reply_to_message_id:
            # The imported reply edge is authoritative.
            edges = db.scalars(select(ReplyEdge).where(ReplyEdge.from_post_id == view.post.id)).all()
            for edge in edges:
                candidate_views = by_post.get(edge.to_post_id, [])
                story_ids = [v.fragment.story_id for v in candidate_views if v.fragment.story_id]
                if not story_ids:
                    continue
                best_id = story_ids[0]
                best_score = -1.0
                for story_id in set(story_ids):
                    score, _ = _similarity(view, [v for v in candidate_views if v.fragment.story_id == story_id])
                    # Reply is a strong edge; use it unless a different reply target cluster is a much better lexical match.
                    score += 0.35
                    if score > best_score:
                        best_score = score
                        best_id = story_id
                target_cluster = db.get(StoryCluster, best_id)
                break

        if view.genre in {"analysis", "opinion"}:
            # Interpretive writing is a separate, labelled article even when it
            # replies to a news item.
            fingerprint = _fingerprint(view)
            cluster = db.scalar(
                select(StoryCluster).where(
                    StoryCluster.channel_id == channel_id, StoryCluster.fingerprint == fingerprint
                )
            )
            if cluster is None:
                cluster = StoryCluster(
                    channel_id=channel_id,
                    canonical_title=view.title or headline_from_text(view.text),
                    summary=view.text[:300],
                    section="analysis" if view.genre == "analysis" else "opinion",
                    genre=view.genre,
                    importance=_importance("analysis" if view.genre == "analysis" else "opinion", [view], view.genre),
                    risk_level="low",
                    status="draft",
                    fingerprint=fingerprint,
                    entity_json=json.dumps(sorted(view.places | view.nouns), ensure_ascii=False),
                )
                db.add(cluster)
                db.flush()
            view.fragment.story_id = cluster.id
            clusters.append(cluster)
            cluster_views.setdefault(cluster.id, []).append(view)
            if target_cluster and target_cluster.id != cluster.id:
                exists = db.scalar(
                    select(StoryRelationship).where(
                        StoryRelationship.story_id == cluster.id,
                        StoryRelationship.related_story_id == target_cluster.id,
                    )
                )
                if not exists:
                    db.add(
                        StoryRelationship(
                            story_id=cluster.id,
                            related_story_id=target_cluster.id,
                            relation="analysis_of" if view.genre == "analysis" else "opinion_on",
                            confidence=0.9,
                        )
                    )
            continue

        cluster = target_cluster
        if cluster is None:
            # Match an existing cluster created in this run.
            best_cluster = None
            best_score = 0.0
            for candidate in clusters:
                if candidate.genre in {"analysis", "opinion"}:
                    continue
                score, shared_place = _similarity(view, cluster_views.get(candidate.id, []))
                if score > best_score:
                    best_score = score
                    best_cluster = candidate
            if best_cluster is not None and best_score >= 0.25:
                cluster = best_cluster

        if cluster is None:
            fingerprint = _fingerprint(view)
            cluster = db.scalar(
                select(StoryCluster).where(
                    StoryCluster.channel_id == channel_id, StoryCluster.fingerprint == fingerprint
                )
            )
            if cluster is None:
                cluster = StoryCluster(
                    channel_id=channel_id,
                    canonical_title=view.title or headline_from_text(view.text),
                    summary=view.text[:300],
                    section=infer_section(view.text, view.topic),
                    genre="news",
                    importance=0.0,
                    risk_level="low",
                    status="draft",
                    fingerprint=fingerprint,
                    entity_json="[]",
                )
                db.add(cluster)
                db.flush()
            clusters.append(cluster)
        view.fragment.story_id = cluster.id
        cluster_views.setdefault(cluster.id, []).append(view)

    # Finalize cluster-level properties from all attached fragments.
    result: list[StoryCluster] = []
    for cluster in clusters:
        if cluster.id in {c.id for c in result}:
            continue
        attached = cluster_views.get(cluster.id, [])
        if not attached:
            continue
        attached.sort(key=lambda v: (v.post.posted_at, v.fragment.sequence))
        section, genre = _section_and_genre(attached[0], attached, None)
        if cluster.genre in {"analysis", "opinion"}:
            section = cluster.section
            genre = cluster.genre
        titles = [v.title for v in attached if v.title]
        seed_titles = [_seed_title(v.fragment) for v in attached if _seed_title(v.fragment)]
        if seed_titles:
            cluster.canonical_title = seed_titles[0]
        elif titles:
            cluster.canonical_title = titles[0]
        elif not cluster.canonical_title:
            cluster.canonical_title = headline_from_text(attached[-1].text)
        cluster.section = section
        cluster.genre = genre
        cluster.importance = _importance(section, attached, genre)
        cluster.risk_level, tags = _risk(" ".join(v.text for v in attached))
        cluster.summary = attached[-1].text[:320]
        places: set[str] = set()
        nouns: set[str] = set()
        for view in attached:
            places |= view.places
            nouns |= view.nouns
        cluster.entity_json = json.dumps(
            {"places": sorted(places), "nouns": sorted(nouns), "risk_tags": tags}, ensure_ascii=False
        )
        cluster.updated_at = utcnow()
        result.append(cluster)
    db.flush()
    return result
