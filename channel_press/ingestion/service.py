from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import active_rights, audit
from ..models import (
    Channel,
    PostFragment,
    PostMedia,
    PostVersion,
    ReplyEdge,
    RightsGrant,
    SourceImport,
    SourcePost,
    User,
)
from ..security import safe_filename, sha256_bytes, sha256_text
from ..timeutil import utcnow
from dataclasses import replace as _dataclass_replace

from ..editorial.moderation import (
    CLASSIFIER_VERSION,
    EDITOR_OVERRIDES,
    classify_message,
    exclusion_for,
)
from .adapters import NormalizedMessage, normalize_messages
from .normalize import clean_text, detect_language, split_segments


@dataclass
class ImportStats:
    import_id: int
    raw_count: int
    accepted: int
    rejected: int
    created: int
    updated: int
    media: int


def _copy_media(
    db: Session,
    post: SourcePost,
    media: dict,
    media_root: Path | None,
    channel_slug: str,
) -> PostMedia | None:
    kind = str(media.get("kind") or "photo").lower()
    filename = str(media.get("file") or "").strip()
    path = ""
    checksum = ""
    width = int(media.get("width") or 0)
    height = int(media.get("height") or 0)
    duration = float(media.get("duration") or 0)
    if filename and media_root:
        source = (media_root / filename).resolve()
        try:
            source.relative_to(media_root.resolve())
        except ValueError:
            return None
        if source.is_file():
            target_dir = Path(db.info.get("media_dir", "data/media")) / channel_slug / str(post.id)
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / safe_filename(source.name)
            shutil.copy2(source, target)
            checksum = sha256_bytes(target.read_bytes())
            path = str(target)
    elif filename:
        # Telegram Desktop export file already staged by the owner.
        candidate = Path(filename)
        if candidate.is_file():
            checksum = sha256_bytes(candidate.read_bytes())
            path = str(candidate)
    if not path and not filename and not str(media.get("telegram_file_id") or "").strip():
        # Nothing to render and no remote handle: do not invent a media record.
        return None
    if path and kind == "photo" and (not width or not height):
        try:
            from PIL import Image

            with Image.open(path) as image:
                width, height = image.size
        except Exception:
            pass
    record = PostMedia(
        post_id=post.id,
        kind="video" if kind in {"video", "animation"} else kind,
        telegram_file_id=str(media.get("telegram_file_id") or ""),
        original_path=path,
        derivative_path="",
        width=width,
        height=height,
        duration_seconds=duration,
        sha256=checksum,
        caption=str(media.get("caption") or ""),
        credit=str(media.get("credit") or "Source channel"),
        source_url=post.source_url,
        is_sensitive=bool(media.get("is_sensitive")),
        is_documentary=bool(media.get("is_documentary", True)),
        label=str(media.get("label") or ""),
    )
    db.add(record)
    return record


def _add_fragments(
    db: Session,
    post: SourcePost,
    message: NormalizedMessage,
    channel_id: int,
) -> int:
    """Create attributable fragments for an included post.

    Fragments are rebuilt only when none exist, so a re-import or a
    reclassification pass never destroys a manual story decision.
    """
    unit_key = (
        f"g:{channel_id}:{message.grouped_id}" if message.grouped_id else f"p:{post.id}"
    )
    processing_message = message
    if message.translation_en:
        # The original remains in raw_json; a source-provided translation is
        # labelled rather than silently replaced by model output.
        processing_message = replace(message, text=message.translation_en, segments=None)
    created = 0
    for fragment in split_segments(processing_message):
        db.add(
            PostFragment(
                post_id=post.id,
                story_id=None,
                text=fragment["text"],
                start_offset=fragment["start"],
                end_offset=fragment["end"],
                language=post.language,
                topics_json=json.dumps(
                    {
                        "topics": fragment["topics"],
                        "title": fragment["title"],
                        "incident_key": fragment["incident_key"],
                        "location": fragment["location"],
                        "genre": fragment["genre"],
                        "article_seed": fragment.get("article_seed"),
                        "translation": message.translator if message.translation_en else "",
                        "original_text": message.text if message.translation_en else "",
                    },
                    ensure_ascii=False,
                ),
                sequence=fragment["sequence"],
                editorial_unit_key=unit_key,
            )
        )
        created += 1
    return created


def import_payload(
    db: Session,
    channel: Channel,
    payload: dict,
    *,
    imported_by: int | None,
    media_root: Path | None = None,
    source_kind: str = "json",
    original_name: str = "",
    rights_grant: RightsGrant | None = None,
    window_key: str = "",
) -> ImportStats:
    messages = normalize_messages(payload)
    if not messages:
        raise ValueError("No messages were found in the import.")
    rights_ok = channel.is_demo or rights_grant is not None
    import_record = SourceImport(
        channel_id=channel.id,
        kind=source_kind,
        original_name=original_name[:255],
        sha256=sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False)),
        imported_by=imported_by,
        raw_count=len(messages),
        rights_grant_id=rights_grant.id if rights_grant else None,
        window_key=window_key[:200],
    )
    db.add(import_record)
    db.flush()
    db.info["media_dir"] = str(Path(db.info.get("media_dir", "data/media")))

    by_message_id: dict[int, SourcePost] = {}
    created = updated = rejected = media_count = 0
    for message in messages:
        moderation = classify_message(message, rights_ok=rights_ok)
        service_event = bool(
            message.raw
            and message.raw.get("type") in {"service", "messageService"}
            and not message.text.strip()
        )
        reason = exclusion_for(moderation, deleted=message.deleted, service_event=service_event)
        existing = db.scalar(
            select(SourcePost).where(
                SourcePost.channel_id == channel.id,
                SourcePost.telegram_message_id == message.message_id,
            )
        )
        text = clean_text(message.text)
        content_hash = sha256_text(text)
        if existing is None and not message.deleted and not message.forward_origin and text.strip():
            # A repeated copy of an already accepted post adds no new evidence.
            twin = db.scalar(
                select(SourcePost).where(
                    SourcePost.channel_id == channel.id,
                    SourcePost.content_hash == content_hash,
                    SourcePost.telegram_message_id != message.message_id,
                )
            )
            if twin is not None and twin.classification == "editorially_relevant":
                reason = "repeated_copy"
                moderation = _dataclass_replace(
                    moderation, classification="duplicate", reason="repeated_copy", score=0
                )
        if message.translation_en:
            # The original remains in raw_json; a source-provided translation is
            # labelled rather than silently replaced by model output.
            raw = dict(message.raw or {})
            raw["translation_en"] = message.translation_en
            raw["translator"] = message.translator
        else:
            raw = message.raw or {}
        source_url = ""
        if channel.is_public and channel.handle:
            source_url = f"https://t.me/{channel.handle.lstrip('@')}/{message.message_id}"
        if existing is None:
            post = SourcePost(
                channel_id=channel.id,
                import_id=import_record.id,
                rights_grant_id=rights_grant.id if rights_grant else None,
                telegram_message_id=message.message_id,
                grouped_id=message.grouped_id,
                posted_at=message.posted_at,
                edited_at=message.edited_at,
                text=text,
                language=moderation.language or detect_language(text),
                source_url=source_url,
                reply_to_message_id=message.reply_to_message_id,
                forward_origin=message.forward_origin,
                is_forwarded=bool(message.forward_origin),
                inclusion_status="excluded" if reason else "included",
                exclusion_reason=reason or "",
                classification=moderation.classification,
                classification_reason=moderation.reason,
                classification_signals_json=json.dumps(moderation.signals, ensure_ascii=False),
                classification_version=CLASSIFIER_VERSION,
                classified_at=utcnow(),
                moderation_status=moderation.profanity,
                raw_json=json.dumps(raw, ensure_ascii=False, default=str),
                content_hash=content_hash,
                deleted_at=utcnow() if message.deleted else None,
            )
            db.add(post)
            db.flush()
            db.add(
                PostVersion(
                    post_id=post.id,
                    version=1,
                    text=text,
                    edited_at=message.edited_at,
                    content_hash=post.content_hash,
                )
            )
            created += 1
        else:
            post = existing
            new_hash = content_hash
            deletion_is_new = bool(message.deleted and post.deleted_at is None)
            if new_hash != post.content_hash or deletion_is_new:
                previous_versions = db.scalar(
                    select(PostVersion).where(PostVersion.post_id == post.id).order_by(PostVersion.version.desc()).limit(1)
                )
                next_version = (previous_versions.version + 1) if previous_versions else 1
                post.text = text
                post.content_hash = new_hash
                post.version = next_version
                post.edited_at = message.edited_at or utcnow()
                post.deleted_at = utcnow() if message.deleted else post.deleted_at
                db.add(
                    PostVersion(
                        post_id=post.id,
                        version=next_version,
                        text=text,
                        edited_at=post.edited_at,
                        content_hash=new_hash,
                    )
                )
                updated += 1
            if post.editor_override:
                # An editor's explicit decision survives re-imports and
                # classifier upgrades; it is recorded in the audit log.
                status, override_reason, classification = EDITOR_OVERRIDES.get(
                    post.editor_override, ("excluded", "editor_excluded", "low_information")
                )
                post.inclusion_status = status
                post.exclusion_reason = override_reason
                post.classification = classification
                post.classification_reason = f"editor_override:{post.editor_override}"
                post.classification_signals_json = json.dumps(
                    [
                        *moderation.signals,
                        {"kind": "editor_override", "values": [post.editor_override]},
                    ],
                    ensure_ascii=False,
                )
                post.classification_version = CLASSIFIER_VERSION
                post.classified_at = utcnow()
            else:
                post.inclusion_status = "excluded" if reason else "included"
                post.exclusion_reason = reason or ""
                post.classification = moderation.classification
                post.classification_reason = moderation.reason
                post.classification_signals_json = json.dumps(moderation.signals, ensure_ascii=False)
                post.classification_version = CLASSIFIER_VERSION
                post.classified_at = utcnow()
                post.moderation_status = moderation.profanity
            post.language = moderation.language or post.language or "en"
            if message.grouped_id:
                post.grouped_id = message.grouped_id
        by_message_id[message.message_id] = post
        if reason:
            rejected += 1
            continue
        # Fragments are rebuilt only when the text changed, so manual story
        # decisions are not silently destroyed by an identical re-import.
        existing_fragments = db.scalar(select(PostFragment).where(PostFragment.post_id == post.id).limit(1))
        if existing_fragments is None:
            _add_fragments(db, post, message, channel.id)
        existing_media = db.scalar(select(PostMedia).where(PostMedia.post_id == post.id).limit(1))
        if existing_media is None:
            for media in message.media or []:
                if _copy_media(db, post, media, media_root, channel.slug):
                    media_count += 1
    # Reply edges, only for posts that were imported in this channel.
    for message in messages:
        if not message.reply_to_message_id:
            continue
        source = by_message_id.get(message.message_id)
        target = by_message_id.get(message.reply_to_message_id)
        if not source or not target:
            continue
        exists = db.scalar(
            select(ReplyEdge).where(
                ReplyEdge.from_post_id == source.id, ReplyEdge.to_post_id == target.id
            )
        )
        if not exists:
            db.add(ReplyEdge(from_post_id=source.id, to_post_id=target.id, edge_type="reply"))
    import_record.accepted_count = len(messages) - rejected
    import_record.rejected_count = rejected
    audit(
        db,
        None,
        "source.import",
        "channel",
        channel.id,
        {"raw": len(messages), "accepted": import_record.accepted_count, "rejected": rejected},
    )
    db.flush()
    return ImportStats(
        import_id=import_record.id,
        raw_count=len(messages),
        accepted=import_record.accepted_count,
        rejected=rejected,
        created=created,
        updated=updated,
        media=media_count,
    )


@dataclass
class ReclassifyStats:
    """Outcome of re-running the deterministic classifier over stored posts."""

    scanned: int = 0
    out_of_scope: int = 0
    current: int = 0
    reclassified: int = 0
    included: int = 0
    excluded: int = 0
    overrides: int = 0
    duplicates: int = 0
    fragments_created: int = 0
    by_classification: dict[str, int] = field(default_factory=dict)


def _stored_message(post: SourcePost, kinds: list[str]) -> NormalizedMessage:
    """Rebuild the classifier input from a stored post without altering it."""
    try:
        raw = json.loads(post.raw_json or "{}")
    except (TypeError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    segments = raw.get("segments")
    segment_meta = raw.get("segment_meta")
    tags = raw.get("tags")
    translation = raw.get("translation_en")
    return NormalizedMessage(
        message_id=post.telegram_message_id,
        posted_at=post.posted_at,
        text=post.text or "",
        edited_at=post.edited_at,
        grouped_id=post.grouped_id,
        reply_to_message_id=post.reply_to_message_id,
        forward_origin=post.forward_origin or "",
        deleted=post.deleted_at is not None,
        segments=segments if isinstance(segments, list) else None,
        segment_meta=segment_meta if isinstance(segment_meta, dict) else None,
        media=[{"kind": kind} for kind in kinds],
        tags=tags if isinstance(tags, list) else None,
        translation_en=translation if isinstance(translation, str) else None,
        translator=str(raw.get("translator") or ""),
        raw=raw,
    )


def _remember_accepted(post: SourcePost, seen_hashes: set[str]) -> None:
    """Track content already accepted so a later copy is labelled a duplicate.

    Media-only posts can have no text; an empty string is not evidence and must
    never make unrelated posts look like repeated copies.
    """
    if (
        post.inclusion_status == "included"
        and post.deleted_at is None
        and not post.forward_origin
        and post.content_hash
        and (post.text or "").strip()
    ):
        seen_hashes.add(post.content_hash)


def reclassify_posts(
    db: Session,
    channel: Channel,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    force: bool = False,
    rights_ok: bool | None = None,
    actor_id: int | None = None,
) -> ReclassifyStats:
    """Re-run the current classifier over stored posts before story building.

    Legacy imports (and posts classified by an older ruleset) otherwise bypass
    new exclusion rules because story building only reads ``inclusion_status``.
    This pass recomputes classification, language and moderation status for any
    stale row, preserves explicit editor overrides, keeps repeated copies out,
    and creates missing fragments for posts that are newly includable.

    All channel posts are walked in chronological order so repeated-copy
    detection sees earlier accepted evidence even when only one window is being
    reclassified. Rows already at :data:`CLASSIFIER_VERSION` are left untouched
    unless ``force`` is set.
    """
    rights = rights_ok
    if rights is None:
        rights = channel.is_demo or active_rights(db, channel.id) is not None
    posts = list(
        db.scalars(
            select(SourcePost)
            .where(SourcePost.channel_id == channel.id)
            .order_by(
                SourcePost.posted_at.asc(),
                SourcePost.telegram_message_id.asc(),
                SourcePost.id.asc(),
            )
        )
    )
    stats = ReclassifyStats(scanned=len(posts))
    if not posts:
        return stats

    kinds_by_post: dict[int, list[str]] = {}
    for post_id, kind in db.execute(
        select(PostMedia.post_id, PostMedia.kind).where(
            PostMedia.post_id.in_([post.id for post in posts])
        )
    ):
        kinds_by_post.setdefault(post_id, []).append(str(kind or ""))

    seen_hashes: set[str] = set()
    for post in posts:
        in_scope = (start is None or post.posted_at >= start) and (
            end is None or post.posted_at < end
        )
        if not in_scope:
            stats.out_of_scope += 1
            _remember_accepted(post, seen_hashes)
            continue
        stale = (
            force
            or post.classification_version != CLASSIFIER_VERSION
            or post.classified_at is None
        )
        if not stale:
            stats.current += 1
            _remember_accepted(post, seen_hashes)
            continue

        if not post.content_hash and (post.text or "").strip():
            post.content_hash = sha256_text(post.text or "")
        message = _stored_message(post, kinds_by_post.get(post.id, []))
        moderation = classify_message(message, rights_ok=rights)
        reason = exclusion_for(moderation, deleted=message.deleted)

        if post.editor_override:
            # An editor's explicit decision survives classifier upgrades.
            status, override_reason, classification = EDITOR_OVERRIDES.get(
                post.editor_override,
                ("excluded", "editor_excluded", "low_information"),
            )
            post.inclusion_status = status
            post.exclusion_reason = override_reason
            post.classification = classification
            post.classification_reason = f"editor_override:{post.editor_override}"
            post.classification_signals_json = json.dumps(
                [
                    *moderation.signals,
                    {"kind": "editor_override", "values": [post.editor_override]},
                ],
                ensure_ascii=False,
            )
            post.classification_version = CLASSIFIER_VERSION
            post.classified_at = utcnow()
            stats.overrides += 1
        else:
            if (
                reason is None
                and moderation.classification == "editorially_relevant"
                and post.content_hash
                and (post.text or "").strip()
                and post.content_hash in seen_hashes
            ):
                # A repeated copy adds no new evidence; the first accepted
                # version stays the citable source.
                moderation = replace(
                    moderation,
                    classification="duplicate",
                    reason="repeated_copy",
                    score=0,
                    signals=[
                        *moderation.signals,
                        {"kind": "duplicate", "values": ["repeated_copy"]},
                    ],
                )
                reason = "repeated_copy"
                stats.duplicates += 1
            post.inclusion_status = "excluded" if reason else "included"
            post.exclusion_reason = reason or ""
            post.classification = moderation.classification
            post.classification_reason = moderation.reason
            post.classification_signals_json = json.dumps(moderation.signals, ensure_ascii=False)
            post.classification_version = CLASSIFIER_VERSION
            post.classified_at = utcnow()
            post.moderation_status = moderation.profanity

        post.language = moderation.language or post.language or "en"
        stats.reclassified += 1
        stats.by_classification[post.classification] = (
            stats.by_classification.get(post.classification, 0) + 1
        )
        if post.inclusion_status == "included":
            stats.included += 1
            if post.deleted_at is None and not post.forward_origin:
                existing = db.scalar(
                    select(PostFragment.id).where(PostFragment.post_id == post.id).limit(1)
                )
                if existing is None:
                    stats.fragments_created += _add_fragments(db, post, message, channel.id)
        else:
            stats.excluded += 1
        _remember_accepted(post, seen_hashes)

    if stats.reclassified:
        actor = db.get(User, actor_id) if actor_id else None
        audit(
            db,
            actor,
            "source.reclassify",
            "channel",
            channel.id,
            {
                "classifier": CLASSIFIER_VERSION,
                "scanned": stats.scanned,
                "reclassified": stats.reclassified,
                "included": stats.included,
                "excluded": stats.excluded,
                "overrides": stats.overrides,
                "duplicates": stats.duplicates,
                "fragments_created": stats.fragments_created,
                "by_classification": stats.by_classification,
            },
        )
    db.flush()
    return stats


def apply_deletion(db: Session, post: SourcePost, reason: str = "deleted") -> None:
    post.deleted_at = utcnow()
    post.inclusion_status = "excluded"
    post.exclusion_reason = reason
    audit(db, None, "source.delete", "source_post", post.id, {"reason": reason})


def accept_edited_post(db: Session, post: SourcePost, new_text: str, actor_id: int | None) -> None:
    new_hash = sha256_text(new_text)
    if new_hash == post.content_hash:
        return
    post.version += 1
    post.text = new_text
    post.content_hash = new_hash
    post.edited_at = utcnow()
    db.add(
        PostVersion(
            post_id=post.id,
            version=post.version,
            text=new_text,
            edited_at=post.edited_at,
            content_hash=new_hash,
        )
    )
    audit(db, None, "source.edit", "source_post", post.id, {"version": post.version})


def source_window(
    db: Session,
    channel_id: int,
    start: datetime,
    end: datetime,
    *,
    include_excluded: bool = False,
) -> list[SourcePost]:
    stmt = select(SourcePost).where(
        SourcePost.channel_id == channel_id,
        SourcePost.posted_at >= start,
        SourcePost.posted_at < end,
    )
    if not include_excluded:
        stmt = stmt.where(SourcePost.inclusion_status == "included")
    stmt = stmt.order_by(SourcePost.posted_at.asc(), SourcePost.telegram_message_id.asc())
    return list(db.scalars(stmt))


def ensure_import_media_dir(db: Session, base: Path) -> None:
    db.info["media_dir"] = str(base)
