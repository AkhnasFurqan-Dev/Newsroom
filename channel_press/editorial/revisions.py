"""Revisioned editorial content and immutable edition versions.

The rules implemented here are the editorial contract of the newsroom:

* ``Article`` is the mutable working draft. Every save increments
  ``Article.revision`` and writes an ``ArticleVersion`` snapshot.
* ``EditionArticle.snapshot_json`` freezes the article content used by a
  published edition version. Later edits therefore cannot alter V1.
* A new version is a new ``Edition`` row (``parent_edition_id`` points at the
  version it supersedes), never a mutation of the published row.
* A rendered preview is tracked separately (``Edition.preview_*``). It is not a
  publication and never changes a published bundle.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    Article,
    ArticleMedia,
    ArticleVersion,
    Edition,
    EditionArticle,
)
from ..timeutil import utcnow

# ``rejected`` is kept as a legacy alias for an excluded article.
EDITORIAL_STATUSES = {
    "draft",
    "needs_review",
    "research",
    "approved",
    "held",
    "excluded",
    "rejected",
}
EXCLUDED_STATUSES = {"excluded", "rejected"}
BLOCKING_STATUSES = {"held", "research"}
STATUS_LABELS = {
    "draft": "Draft",
    "needs_review": "Needs review",
    "research": "Needs research",
    "approved": "Approved",
    "held": "Held",
    "excluded": "Excluded",
    "rejected": "Excluded (legacy)",
}


class RevisionConflict(ValueError):
    """Another editor saved a newer revision than the one being submitted."""


def article_media(db: Session, article_id: int) -> list[ArticleMedia]:
    return list(
        db.scalars(
            select(ArticleMedia)
            .where(ArticleMedia.article_id == article_id)
            .order_by(ArticleMedia.role.asc(), ArticleMedia.id.asc())
        )
    )


def article_snapshot(db: Session, article: Article) -> dict[str, Any]:
    """A complete, self-contained description of the printed article."""
    try:
        body = json.loads(article.body_json or "[]")
    except json.JSONDecodeError:
        body = [paragraph for paragraph in (article.body_text or "").split("\n\n") if paragraph.strip()]
    media = []
    for row in article_media(db, article.id):
        media.append(
            {
                "id": row.id,
                "media_id": row.media_id,
                "role": row.role,
                "caption": row.caption,
                "credit": row.credit,
                "path": row.path,
                "checksum": row.checksum,
                "graphic_kind": row.graphic_kind,
                "frame_seconds": row.frame_seconds,
                "selected": bool(row.selected),
            }
        )
    return {
        "article_id": article.id,
        "story_id": article.story_id,
        "revision": article.revision,
        "title": article.title,
        "deck": article.deck,
        "standfirst": article.standfirst,
        "body": [str(paragraph) for paragraph in body if str(paragraph).strip()],
        "byline": article.byline,
        "genre": article.genre,
        "section": article.section,
        "status": article.status,
        "locked": bool(article.locked),
        "word_count": article.word_count,
        "media": media,
    }


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _split_body(body_text: str) -> list[str]:
    body = [paragraph.strip() for paragraph in (body_text or "").split("\n\n") if paragraph.strip()]
    if not body:
        raise ValueError("Article body cannot be empty.")
    return body


def save_article_revision(
    db: Session,
    article: Article,
    *,
    title: str,
    deck: str,
    standfirst: str,
    body_text: str,
    actor_id: int | None,
    expected_revision: int | None = None,
    byline: str | None = None,
    section: str | None = None,
    genre: str | None = None,
    status: str | None = None,
    lock: bool | None = None,
) -> Article:
    if expected_revision is not None and expected_revision != article.revision:
        raise RevisionConflict(
            f"Revision conflict: you edited revision {expected_revision} but revision "
            f"{article.revision} is now current. Reload the article and merge your changes."
        )
    body = _split_body(body_text)
    if not title.strip():
        raise ValueError("The headline cannot be empty.")
    if status is not None and status not in EDITORIAL_STATUSES:
        raise ValueError(f"Unknown article status: {status}")
    if section is not None and not section.strip():
        raise ValueError("Choose a section.")
    before = article_snapshot(db, article)
    article.title = title.strip()[:300]
    article.deck = deck.strip()[:300]
    article.standfirst = standfirst.strip()
    article.body_json = json.dumps(body, ensure_ascii=False)
    article.body_text = "\n\n".join(body)
    article.word_count = len(article.body_text.split())
    if byline is not None:
        article.byline = byline.strip()[:120]
    if section is not None:
        article.section = section.strip()[:40]
    if genre is not None and genre in {"news", "analysis", "opinion", "editor_note", "brief"}:
        article.genre = genre
    if status is not None:
        article.status = status
    if lock is not None:
        article.locked = lock
    article.revision += 1
    article.updated_at = utcnow()
    after = article_snapshot(db, article)
    db.add(
        ArticleVersion(
            article_id=article.id,
            revision=article.revision,
            snapshot_json=json.dumps(after, ensure_ascii=False),
            actor_id=actor_id,
        )
    )
    db.flush()
    # Keep a field-level diff on the version record for the history panel. The
    # ArticleVersion table stores the full snapshot; this is derived on demand.
    _ = before
    return article


def version_diff(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, str]]:
    fields = [
        ("title", "Headline"),
        ("deck", "Deck"),
        ("standfirst", "Standfirst"),
        ("body", "Body"),
        ("byline", "Byline"),
        ("section", "Section"),
        ("genre", "Genre"),
        ("status", "Status"),
    ]
    changes: list[dict[str, str]] = []
    for key, label in fields:
        old = before.get(key, "")
        new = after.get(key, "")
        if isinstance(old, list):
            old = "\n\n".join(str(item) for item in old)
        if isinstance(new, list):
            new = "\n\n".join(str(item) for item in new)
        if old != new:
            changes.append({"field": key, "label": label, "before": str(old), "after": str(new)})
    old_media = {(row.get("media_id"), row.get("caption")) for row in before.get("media", [])}
    new_media = {(row.get("media_id"), row.get("caption")) for row in after.get("media", [])}
    if old_media != new_media:
        changes.append(
            {
                "field": "media",
                "label": "Media",
                "before": f"{len(old_media)} asset(s)",
                "after": f"{len(new_media)} asset(s)",
            }
        )
    return changes


def revision_history(db: Session, article: Article, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(ArticleVersion)
            .where(ArticleVersion.article_id == article.id)
            .order_by(ArticleVersion.revision.desc())
            .limit(limit)
        )
    )
    history: list[dict[str, Any]] = []
    for row in rows:
        try:
            snapshot = json.loads(row.snapshot_json or "{}")
        except json.JSONDecodeError:
            snapshot = {}
        history.append(
            {
                "revision": row.revision,
                "created_at": row.created_at,
                "actor_id": row.actor_id,
                "snapshot": snapshot,
                "diff": [],
            }
        )
    # Rows are newest-first. A revision's diff describes what changed from the
    # next older row into that row, so each newest entry gets the comparison
    # against its predecessor rather than an empty "no previous" diff.
    for index in range(len(history) - 1):
        newer = history[index]["snapshot"]
        older = history[index + 1]["snapshot"]
        history[index]["diff"] = version_diff(older, newer)
    return history


def freeze_edition_articles(db: Session, edition: Edition) -> None:
    """Freeze every linked article at its current revision for this version."""
    links = list(
        db.scalars(select(EditionArticle).where(EditionArticle.edition_id == edition.id))
    )
    for link in links:
        article = db.get(Article, link.article_id)
        if article is None:
            continue
        link.article_revision = article.revision
        link.snapshot_json = json.dumps(article_snapshot(db, article), ensure_ascii=False)
    db.flush()


def edition_content_fingerprint(db: Session, edition: Edition) -> int:
    revisions = list(
        db.scalars(
            select(Article.revision)
            .join(EditionArticle, EditionArticle.article_id == Article.id)
            .where(EditionArticle.edition_id == edition.id)
        )
    )
    return int(sum(revisions) + len(revisions))


def mark_preview_stale(db: Session, edition: Edition) -> None:
    if edition.preview_dir and edition.preview_pdf_path:
        edition.preview_state = "stale"
    else:
        edition.preview_state = "missing"
    db.flush()


def edition_theme_hash(db: Session, edition: Edition) -> str:
    from ..publications import publication_for_edition
    from ..theming import payload_hash, theme_payload, current_theme

    publication = publication_for_edition(db, edition)
    if publication is None:
        return ""
    return payload_hash(theme_payload(current_theme(db, publication)))


def preview_matches_content(db: Session, edition: Edition) -> bool:
    return (
        edition.preview_state == "current"
        and bool(edition.preview_dir)
        and edition.preview_revision == edition_content_fingerprint(db, edition)
        and bool(edition.preview_theme_hash)
        and edition.preview_theme_hash == edition_theme_hash(db, edition)
    )


def edition_versions(db: Session, edition: Edition) -> list[Edition]:
    """All versions of the same issue in ascending order."""
    rows = list(
        db.scalars(
            select(Edition)
            .where(
                Edition.channel_id == edition.channel_id,
                Edition.kind == edition.kind,
                Edition.issue_number == edition.issue_number,
            )
            .order_by(Edition.version.asc(), Edition.id.asc())
        )
    )
    return rows or [edition]


def latest_version(db: Session, edition: Edition) -> Edition:
    rows = edition_versions(db, edition)
    return rows[-1]


def working_revision(db: Session, edition: Edition, *, actor_id: int | None = None) -> Edition:
    """Return this edition if it is already a working draft, else create V(n+1).

    Only a published edition needs a new row. A draft or awaiting-review edition
    is already editable and is returned unchanged.
    """
    if edition.status != "published":
        return edition
    versions = edition_versions(db, edition)
    next_number = max(item.version for item in versions) + 1
    existing = next(
        (item for item in versions if item.version == next_number and item.status != "published"),
        None,
    )
    if existing is not None:
        return existing
    revision = Edition(
        channel_id=edition.channel_id,
        kind=edition.kind,
        title=edition.title,
        volume=edition.volume,
        issue_number=edition.issue_number,
        status="draft",
        window_start=edition.window_start,
        window_end=edition.window_end,
        cutoff_at=edition.cutoff_at,
        target_release_at=edition.target_release_at,
        timezone=edition.timezone,
        version=next_number,
        parent_edition_id=edition.id,
        correction_note=f"Working revision of version {edition.version}.",
        profile_snapshot_json=edition.profile_snapshot_json,
        config_snapshot_json=edition.config_snapshot_json,
        qr_policy=edition.qr_policy,
        preview_state="missing",
        created_by=actor_id,
    )
    db.add(revision)
    db.flush()
    links = list(
        db.scalars(
            select(EditionArticle)
            .where(EditionArticle.edition_id == edition.id)
            .order_by(EditionArticle.sort_order.asc())
        )
    )
    for link in links:
        article = db.get(Article, link.article_id)
        snapshot = json.loads(link.snapshot_json or "{}")
        if not snapshot and article is not None:
            snapshot = article_snapshot(db, article)
        db.add(
            EditionArticle(
                edition_id=revision.id,
                article_id=link.article_id,
                section=link.section,
                sort_order=link.sort_order,
                page_start=link.page_start,
                is_front=link.is_front,
                article_revision=(article.revision if article else link.article_revision),
                snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            )
        )
    db.flush()
    return revision


def reorder_article(db: Session, edition: Edition, link: EditionArticle, direction: int) -> None:
    links = list(
        db.scalars(
            select(EditionArticle)
            .where(EditionArticle.edition_id == edition.id)
            .order_by(EditionArticle.sort_order.asc(), EditionArticle.id.asc())
        )
    )
    index = next((position for position, row in enumerate(links) if row.id == link.id), -1)
    target = index + direction
    if index < 0 or target < 0 or target >= len(links):
        return
    links[index], links[target] = links[target], links[index]
    for position, row in enumerate(links):
        row.sort_order = position
    edition.preview_state = "stale" if edition.preview_dir else "missing"
    db.flush()


def set_article_status(db: Session, article: Article, status: str, *, actor_id: int | None = None) -> None:
    if status not in EDITORIAL_STATUSES:
        raise ValueError(f"Unknown article status: {status}")
    article.status = status
    article.updated_at = utcnow()
    article.revision += 1
    snapshot = article_snapshot(db, article)
    db.add(
        ArticleVersion(
            article_id=article.id,
            revision=article.revision,
            snapshot_json=json.dumps(snapshot, ensure_ascii=False),
            actor_id=actor_id,
        )
    )
    db.flush()


def count_versions(db: Session, channel_id: int, kind: str, issue_number: int) -> int:
    value = db.scalar(
        select(func.max(Edition.version)).where(
            Edition.channel_id == channel_id,
            Edition.kind == kind,
            Edition.issue_number == issue_number,
        )
    )
    return int(value or 0)
