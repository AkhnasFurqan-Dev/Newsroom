from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import audit
from ..models import (
    Channel,
    ChannelOwnership,
    PostFragment,
    PostMedia,
    PostVersion,
    ReplyEdge,
    RightsGrant,
    SourceImport,
    SourcePost,
)
from ..security import safe_filename, sha256_bytes, sha256_text
from ..timeutil import utcnow
from .adapters import NormalizedMessage, normalize_messages
from .normalize import clean_text, detect_language, exclusion_reason, split_segments


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
    if not path and not filename:
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
    )
    db.add(import_record)
    db.flush()
    db.info["media_dir"] = str(Path(db.info.get("media_dir", "data/media")))

    by_message_id: dict[int, SourcePost] = {}
    created = updated = rejected = media_count = 0
    for message in messages:
        reason = exclusion_reason(message, rights_ok=rights_ok)
        existing = db.scalar(
            select(SourcePost).where(
                SourcePost.channel_id == channel.id,
                SourcePost.telegram_message_id == message.message_id,
            )
        )
        text = clean_text(message.text)
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
                language=detect_language(text),
                source_url=source_url,
                reply_to_message_id=message.reply_to_message_id,
                forward_origin=message.forward_origin,
                is_forwarded=bool(message.forward_origin),
                inclusion_status="excluded" if reason else "included",
                exclusion_reason=reason or "",
                raw_json=json.dumps(raw, ensure_ascii=False, default=str),
                content_hash=sha256_text(text),
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
            new_hash = sha256_text(text)
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
            post.inclusion_status = "excluded" if reason else "included"
            post.exclusion_reason = reason or ""
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
            unit_key = f"g:{channel.id}:{message.grouped_id}" if message.grouped_id else f"p:{post.id}"
            processing_message = message
            if message.translation_en:
                processing_message = replace(message, text=message.translation_en, segments=None)
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
