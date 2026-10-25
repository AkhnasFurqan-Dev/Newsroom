from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Channel, PostFragment, PostMedia, SourcePost, StoryCluster
from ..timeutil import utcnow


@dataclass
class MediaChoice:
    media: PostMedia
    score: float
    reasons: list[str]
    is_video_frame: bool = False
    frame_seconds: float = 0.0


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def image_metrics(path: Path) -> dict:
    with Image.open(path) as image:
        width, height = image.size
        gray = image.convert("L")
        small = gray.resize((min(width, 640), max(1, int(min(width, 640) * height / max(width, 1)))))
        pixels = list(small.getdata())
        brightness = sum(pixels) / len(pixels) if pixels else 0
        variance = sum((p - brightness) ** 2 for p in pixels) / len(pixels) if pixels else 0
    try:
        import cv2

        matrix = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        sharpness = float(cv2.Laplacian(matrix, cv2.CV_64F).var()) if matrix is not None else 0.0
    except Exception:
        sharpness = 0.0
    return {
        "width": width,
        "height": height,
        "brightness": round(brightness, 1),
        "variance": round(variance, 1),
        "sharpness": round(sharpness, 1),
        "aspect": round(width / height, 3) if height else 0,
    }


def make_derivative(source: Path, target_dir: Path, name: str, *, max_width: int = 2200, crop_ratio: float = 1.5) -> Path:
    """Create a reversible, documented print derivative. The original is kept."""
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    with Image.open(source) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        width, height = image.size
        ratio = width / height if height else 1
        adjustments = []
        if abs(ratio - crop_ratio) > 0.02:
            if ratio > crop_ratio:
                new_width = int(height * crop_ratio)
                left = (width - new_width) // 2
                image = image.crop((left, 0, left + new_width, height))
                adjustments.append(f"center crop from {width}x{height} to {image.width}x{image.height}")
            else:
                new_height = int(width / crop_ratio)
                top = (height - new_height) // 2
                image = image.crop((0, top, width, top + new_height))
                adjustments.append(f"center crop from {width}x{height} to {image.width}x{image.height}")
        if image.width > max_width:
            height = int(image.height * max_width / image.width)
            image = image.resize((max_width, height), Image.Resampling.LANCZOS)
            adjustments.append(f"resized to {max_width}px wide")
        image.save(target, "JPEG", quality=92, optimize=True)
    (target.with_suffix(target.suffix + ".json")).write_text(
        json.dumps(
            {
                "source": str(source),
                "source_sha256": _checksum(source),
                "derivative_sha256": _checksum(target),
                "adjustments": adjustments or ["format conversion only"],
                "created_at": utcnow().isoformat(),
                "content_generation": "none",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def _story_posts(db: Session, story: StoryCluster) -> list[SourcePost]:
    rows = db.execute(
        select(SourcePost, PostFragment.editorial_unit_key)
        .join(PostFragment, PostFragment.post_id == SourcePost.id)
        .where(PostFragment.story_id == story.id)
    ).all()
    posts: dict[int, SourcePost] = {}
    unit_keys: set[str] = set()
    for post, unit_key in rows:
        posts[post.id] = post
        if unit_key:
            unit_keys.add(unit_key)
    if unit_keys:
        album_rows = db.execute(
            select(SourcePost, PostFragment.editorial_unit_key)
            .join(PostFragment, PostFragment.post_id == SourcePost.id)
            .where(PostFragment.editorial_unit_key.in_(unit_keys))
        ).all()
        for post, _ in album_rows:
            posts[post.id] = post
    # Album members with no text have no fragment; pull grouped media too.
    group_ids = {post.grouped_id for post in posts.values() if post.grouped_id}
    if group_ids:
        for post in db.scalars(
            select(SourcePost).where(SourcePost.channel_id == story.channel_id, SourcePost.grouped_id.in_(group_ids))
        ):
            posts[post.id] = post
    return list(posts.values())


def _declared_bytes(post: SourcePost, file_id: str) -> int:
    try:
        raw = json.loads(post.raw_json or "{}")
    except (TypeError, ValueError):
        return 0
    for item in raw.get("media", []) or []:
        if str(item.get("telegram_file_id") or "") == str(file_id):
            try:
                return int(item.get("bytes") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


def _metadata_text(media: PostMedia) -> str:
    return f"{media.caption or ''} {media.label or ''}".casefold()


def _media_has_permission(db: Session, channel: Channel) -> bool:
    """Rights gate for downloading source media.

    A demo channel carries synthetic, self-authored plates. A real channel
    needs an active rights grant whose scope covers ingestion or publication;
    an API login alone is never treated as permission.
    """
    if channel.is_demo:
        return True
    from ..auth import active_rights

    grant = active_rights(db, channel.id)
    if grant is None or grant.revoked_at is not None:
        return False
    try:
        scopes = json.loads(grant.scope_json or "[]")
    except (TypeError, ValueError):
        scopes = []
    return bool({"ingest", "publish", "ai_process"} & {str(scope) for scope in scopes})


def _media_is_eligible(media: PostMedia, *, post: SourcePost) -> tuple[bool, str]:
    """Cheap relevance policy applied before any network request.

    Source posts can contain logos, stickers, documents and promotional art.
    Those are not evidence photographs and must not be fetched merely because
    they exist in the channel history.
    """
    if media.kind not in {"photo", "image", "video", "animation"}:
        return False, "unsupported_media_kind"
    if media.is_sensitive:
        return False, "sensitive_requires_editor"
    if not media.is_documentary:
        return False, "non_documentary_asset"
    text = _metadata_text(media)
    promotional = (
        "logo", "promo", "advert", "subscribe", "join our", "sponsor",
        "banner", "poster", "sale", "discount", "click the link",
    )
    if any(term in text for term in promotional):
        return False, "promotional_asset"
    if media.kind in {"video", "animation"}:
        declared = _declared_bytes(post, media.telegram_file_id) / (1024 * 1024)
        if declared > min(settings.telegram_max_video_mb, settings.telegram_max_media_mb):
            return False, "over_size_limit"
    else:
        declared = _declared_bytes(post, media.telegram_file_id)
        if declared > settings.telegram_max_media_mb * 1024 * 1024:
            return False, "over_size_limit"
    if media.download_attempts >= max(1, settings.media_download_attempts):
        return False, "retry_limit_reached"
    return True, ""


def _media_relevance(media: PostMedia, story_text: str) -> float:
    """Metadata-only ranking used to bound how many assets are downloaded."""
    score = 0.0
    if media.kind in {"photo", "image"}:
        score += 1.0
    if media.is_documentary:
        score += 1.0
    caption = (media.caption or "").strip()
    if len(caption) >= 12:
        score += 0.6
    tokens = set(caption.casefold().split())
    overlap = tokens & set(story_text.casefold().split())
    score += min(len(overlap) * 0.25, 1.5)
    return round(score, 3)


def ensure_media_files(db: Session, story: StoryCluster, *, edition_id: int | None = None) -> int:
    """Fetch a bounded set of relevant source media for one story.

    This is the only place an edition may ask Telegram for bytes. It runs in
    the dedicated preparation stage, before layout, with a per-asset attempt
    limit, a declared-size cap and a rights gate. The renderer only ever reads
    the local paths written here.
    """
    if not settings.telegram_download_media:
        return 0
    posts = {post.id: post for post in _story_posts(db, story)}
    if not posts:
        return 0
    media_rows = list(
        db.scalars(select(PostMedia).where(PostMedia.post_id.in_(list(posts.keys()))))
    )
    if not media_rows:
        return 0
    from ..telegram.service import telegram_service

    story_text = " ".join((post.text or "") for post in posts.values()) + " " + story.canonical_title
    cached = [media for media in media_rows if media.original_path and Path(media.original_path).exists()]
    candidates = [
        media
        for media in media_rows
        if media not in cached and media.telegram_file_id and posts.get(media.post_id) is not None
    ]
    candidates.sort(key=lambda media: _media_relevance(media, story_text), reverse=True)
    budget = max(0, settings.media_max_assets_per_story - len(cached))
    downloaded = 0
    for media in candidates:
        if downloaded >= budget:
            break
        post = posts.get(media.post_id)
        if post is None or not post.telegram_message_id:
            continue
        channel = db.get(Channel, post.channel_id)
        handle = (channel.handle if channel else "") or settings.telegram_source_username
        if channel is None or channel.is_demo or not handle:
            continue
        if not _media_has_permission(db, channel):
            media.download_state = "rights_blocked"
            media.last_download_error = "No active rights grant covers source-media use."
            continue
        if not telegram_service.credentials_configured():
            continue
        eligible, reason = _media_is_eligible(media, post=post)
        if not eligible:
            media.download_state = "skipped"
            media.last_download_error = reason
            continue
        suffix = (
            ".jpg"
            if media.kind in {"photo", "image"}
            else ".mp4"
            if media.kind in {"video", "animation"}
            else ".bin"
        )
        target = settings.media_dir / channel.slug / str(post.id) / f"telegram_{media.id}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        attempts = max(1, settings.media_download_attempts)
        error = ""
        for _ in range(attempts):
            media.download_attempts += 1
            if db.info.get("durable_worker"):
                db.commit()
            try:
                # The gateway applies its own call timeout. A failure leaves the
                # story text-only rather than stalling the edition.
                telegram_service.download_media(handle, post.telegram_message_id, target)
                error = ""
                break
            except Exception as exc:  # noqa: BLE001 - media failure must not fail the edition
                error = f"{type(exc).__name__}: {str(exc)[:240]}"
        if error:
            media.last_download_error = error
            media.download_state = "failed"
            continue
        if not target.exists() or target.stat().st_size == 0:
            media.last_download_error = "Downloaded file was empty."
            media.download_state = "failed"
            continue
        media.original_path = str(target)
        media.sha256 = _checksum(target)
        media.bytes = target.stat().st_size
        media.original_filename = target.name
        media.downloaded_at = utcnow()
        media.download_state = "downloaded"
        media.last_download_error = ""
        if channel is not None:
            from ..auth import active_rights

            grant = active_rights(db, channel.id)
            media.rights_grant_id = grant.id if grant else media.rights_grant_id
        if edition_id is not None:
            media.edition_id = edition_id
            media.retention_state = "edition_working_media"
        if media.kind in {"photo", "image"}:
            try:
                with Image.open(target) as image:
                    media.width, media.height = image.size
            except Exception:  # noqa: BLE001
                pass
        downloaded += 1
    if downloaded or candidates:
        db.flush()
    return downloaded


def _media_for_story(db: Session, story: StoryCluster) -> list[PostMedia]:
    posts = _story_posts(db, story)
    if not posts:
        return []
    return list(db.scalars(select(PostMedia).where(PostMedia.post_id.in_([post.id for post in posts]))))


def prepare_edition_media(
    db: Session,
    edition_id: int,
    stories: list[StoryCluster],
    *,
    job_id: int | None = None,
    progress=None,
) -> dict:
    """Bounded, resumable media preparation performed *before* layout.

    The renderer never touches the network: it only reads files this stage has
    already placed on disk. Already-cached files are reused without a second
    download; a failed asset is recorded and never blocks the text path.
    """
    stats = {
        "stories": len(stories),
        "discovered": 0,
        "selected": 0,
        "downloaded": 0,
        "skipped_cached": 0,
        "skipped_policy": 0,
        "failed": 0,
        "bytes_downloaded": 0,
    }
    if progress:
        progress("media_prepare", f"Evaluating media relevance for {len(stories)} stories")
    for story_index, story in enumerate(stories, start=1):
        if job_id is not None:
            from ..operations import check_job_control

            check_job_control(job_id)
        media_rows = _media_for_story(db, story)
        stats["discovered"] += len(media_rows)
        before_errors = {media.id: media.last_download_error for media in media_rows}
        cached = sum(1 for media in media_rows if media.original_path and Path(media.original_path).exists())
        stats["skipped_cached"] += cached
        if progress:
            progress(
                "media_download",
                f"Story {story_index}/{len(stories)}: {len(media_rows)} media discovered, {cached} cached",
            )
        downloaded = ensure_media_files(db, story, edition_id=edition_id)
        stats["downloaded"] += downloaded
        refreshed = _media_for_story(db, story)
        story_failed = 0
        for media in refreshed:
            if media.original_path and Path(media.original_path).exists():
                try:
                    stats["bytes_downloaded"] += Path(media.original_path).stat().st_size
                except OSError:
                    pass
            if media.last_download_error and media.last_download_error != before_errors.get(media.id):
                story_failed += 1
        stats["failed"] += story_failed
        eligible = sum(1 for media in refreshed if media.original_path and Path(media.original_path).exists())
        stats["selected"] += eligible
        stats["skipped_policy"] += max(0, len(media_rows) - cached - downloaded - story_failed)
        if progress:
            progress(
                "media_process",
                f"Story {story_index}/{len(stories)}: {eligible} asset(s) ready for layout, "
                f"{story_failed} failed",
            )
    db.flush()
    return stats


def _safe_cache_file(raw_path: str) -> Path | None:
    """Resolve a stored path strictly inside the configured media directory."""
    if not raw_path:
        return None
    path = Path(raw_path).resolve()
    try:
        path.relative_to(settings.media_dir.resolve())
    except ValueError:
        return None  # outside the media tree: never touch it
    return path if path.is_file() else None


def _edition_output_complete(edition) -> bool:
    """True when the published PDF and every e-paper page are readable locally.

    A complete bundle embeds its images, so the cached source file is no longer
    required. Anything less complete stays protected.
    """
    if not edition.epaper_dir or not edition.pdf_path:
        return False
    if not Path(edition.pdf_path).is_file():
        return False
    root = Path(edition.epaper_dir)
    index = root / "index.html"
    manifest_path = root / "manifest.json"
    if not index.is_file() or not manifest_path.is_file():
        return False
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    pages = manifest.get("pages") or []
    if not pages:
        return False
    return all((root / str(page.get("image") or "")).is_file() for page in pages)


def _protected_media_ids(db: Session) -> set[int]:
    """Media referenced by any edition that may still need the local file.

    Published output is self-contained and therefore no longer keeps its cache
    alive. Draft, awaiting-review and running editions do. Editor-locked
    articles always protect their assets. Media ingested within the configured
    retention window is also protected so an active collection cannot race a
    cache clear before its edition is built.
    """
    from datetime import timedelta

    from ..models import Article, ArticleMedia, Edition, EditionArticle, Job

    protected: set[int] = set()
    rows = db.execute(
        select(ArticleMedia.media_id, Edition.id, Edition.status, Article.locked)
        .join(Article, ArticleMedia.article_id == Article.id)
        .join(EditionArticle, EditionArticle.article_id == Article.id)
        .join(Edition, EditionArticle.edition_id == Edition.id)
        .where(ArticleMedia.media_id.is_not(None))
    ).all()
    editions: dict[int, object] = {}
    for media_id, edition_id, status, locked in rows:
        if media_id is None:
            continue
        if locked:
            protected.add(media_id)
            continue
        if status not in {"published", "archived"}:
            protected.add(media_id)
            continue
        edition = editions.get(edition_id)
        if edition is None:
            edition = db.get(Edition, edition_id)
            editions[edition_id] = edition
        if edition is not None and not _edition_output_complete(edition):
            protected.add(media_id)
    active_editions = list(
        db.scalars(
            select(Job.edition_id).where(
                Job.status.in_(["scheduled", "running", "queued", "resumable", "failed_retryable"]),
                Job.edition_id.is_not(None),
            )
        )
    )
    if active_editions:
        active_ids = set(active_editions)
        # Protect media already claimed by the active edition, even before the
        # article-media link exists. Media preparation writes this link first;
        # layout writes ArticleMedia later.
        direct_rows = db.scalars(
            select(PostMedia.id).where(PostMedia.edition_id.in_(active_ids))
        )
        protected.update(media_id for media_id in direct_rows if media_id is not None)
        active_rows = db.execute(
            select(ArticleMedia.media_id)
            .join(Article, ArticleMedia.article_id == Article.id)
            .join(EditionArticle, EditionArticle.article_id == Article.id)
            .where(
                ArticleMedia.media_id.is_not(None),
                EditionArticle.edition_id.in_(active_ids),
            )
        ).scalars()
        protected.update(media_id for media_id in active_rows if media_id is not None)
    # A media row can be created at import time and downloaded much later. Use
    # the download time as well, so a just-fetched asset cannot be cleared by a
    # concurrent request before its edition links it.
    edition_scoped = db.execute(
        select(PostMedia.id, PostMedia.edition_id).where(PostMedia.edition_id.is_not(None))
    ).all()
    for media_id, edition_id in edition_scoped:
        edition = editions.get(edition_id)
        if edition is None:
            edition = db.get(Edition, edition_id)
            editions[edition_id] = edition
        if (
            edition is None
            or edition.status not in {"published", "archived"}
            or not _edition_output_complete(edition)
        ):
            protected.add(media_id)
    hours = max(0, int(getattr(settings, "media_retention_hours", 0) or 0))
    if hours:
        cutoff = utcnow() - timedelta(hours=hours)
        recent = db.scalars(
            select(PostMedia.id).where(
                or_(
                    PostMedia.created_at >= cutoff,
                    PostMedia.downloaded_at >= cutoff,
                ),
                PostMedia.original_path.is_not(""),
            )
        )
        protected.update(media_id for media_id in recent if media_id is not None)
    return protected


def _cache_paths(media: PostMedia, *, include_derivatives: bool) -> list[str]:
    paths = [media.original_path]
    if include_derivatives and media.derivative_path:
        paths.append(media.derivative_path)
    return [path for path in paths if path]


def media_cache_summary(db: Session, *, include_derivatives: bool = True, mode: str = "unused") -> dict:
    """Dry-run view of what a cache clear would and would not remove.

    Shows deletable vs. protected files and bytes. Protected means referenced
    by a draft/review/running edition, an editor lock, an active job, or a
    published output whose bundle is not verifiably complete.
    """
    rows = list(db.scalars(select(PostMedia)))
    protected_ids = _protected_media_ids(db)
    deletable_files = deletable_bytes = protected_files = protected_bytes = 0
    for media in rows:
        if media.deleted_at is not None:
            continue
        if mode == "derivatives" and media.derivative_path:
            raw_paths = [media.derivative_path]
        else:
            raw_paths = _cache_paths(media, include_derivatives=include_derivatives)
        resolved = [
            path
            for path in (_safe_cache_file(raw) for raw in raw_paths)
            if path is not None
        ]
        if not _row_allowed(media, db, mode=mode):
            protected_files += len(resolved)
            protected_bytes += sum(path.stat().st_size for path in resolved)
            continue
        if media.id in protected_ids:
            protected_files += len(resolved)
            protected_bytes += sum(path.stat().st_size for path in resolved)
        else:
            deletable_files += len(resolved)
            deletable_bytes += sum(path.stat().st_size for path in resolved)
    return {
        "files": deletable_files,
        "bytes": deletable_bytes,
        "protected_files": protected_files,
        "protected_bytes": protected_bytes,
        "total_rows": len(rows),
        "protected_refs": len(protected_ids),
        "active_job_refs": len(protected_ids),
        "mode": mode,
    }


def _row_allowed(media: PostMedia, db: Session, *, mode: str) -> bool:
    """Which retention categories a given clear mode may touch."""
    from ..models import Edition

    if media.deleted_at is not None:
        return False
    if mode == "unused":
        return media.retention_state in {"shared_source_cache", "derivative_cache"}
    if mode == "derivatives":
        return bool(media.derivative_path)
    if mode == "edition_working":
        if media.retention_state != "edition_working_media":
            return False
        edition = db.get(Edition, media.edition_id) if media.edition_id else None
        return bool(edition and edition.status in {"published", "archived"} and _edition_output_complete(edition))
    if mode == "all":
        if media.retention_state == "edition_working_media":
            edition = db.get(Edition, media.edition_id) if media.edition_id else None
            return bool(edition and edition.status in {"published", "archived"} and _edition_output_complete(edition))
        return media.retention_state in {"shared_source_cache", "derivative_cache"}
    return False


def clear_media_cache(db: Session, *, include_derivatives: bool = True, mode: str = "unused") -> dict:
    """Delete only unreferenced cache files; never remove source or provenance rows.

    Deleting a cached file never deletes the editorial evidence record: the
    SourcePost row, its text, checksums, rights links and audit history all
    remain. Media whose deletion state is uncertain (unknown retention state,
    already deleted, protected edition ownership) is skipped, as is anything
    outside the configured media directory. The operation is idempotent: a
    second run finds no remaining paths and reports the same provenance state.
    """
    rows = list(db.scalars(select(PostMedia)))
    protected_ids = _protected_media_ids(db)
    result = {"deleted": 0, "bytes": 0, "skipped": 0, "failed": 0, "deleted_files": []}
    for media in rows:
        if media.deleted_at is not None or not _row_allowed(media, db, mode=mode):
            result["skipped"] += 1
            continue
        if media.id in protected_ids:
            result["skipped"] += 1
            continue
        touched = False
        delete_derivatives = include_derivatives or mode in {"derivatives", "edition_working", "all"}
        if mode == "derivatives" and media.derivative_path:
            raw_paths = [media.derivative_path]
        else:
            raw_paths = _cache_paths(media, include_derivatives=delete_derivatives)
        for raw_path in raw_paths:
            path = _safe_cache_file(raw_path)
            if path is None:
                # Outside the tree or missing: never follow a path out of the
                # configured media directory. A missing file is not a failure.
                if raw_path and Path(raw_path).exists():
                    result["failed"] += 1
                continue
            try:
                size = path.stat().st_size
                path.unlink()
                result["deleted"] += 1
                result["bytes"] += size
                result["deleted_files"].append(str(path))
                touched = True
            except OSError:
                result["failed"] += 1
        if touched:
            # Provenance survives: keep the row, checksums, rights and source.
            if mode == "derivatives":
                media.derivative_path = ""
            else:
                media.original_path = ""
                if delete_derivatives:
                    media.derivative_path = ""
                media.download_state = "deleted"
                if media.derivative_path:
                    # The source file is gone but a separately retained
                    # derivative remains; keep the row eligible for a later
                    # derivative-only cleanup.
                    media.retention_state = "derivative_cache"
                else:
                    media.deleted_at = utcnow()
                    media.retention_state = "cache_cleared"
    db.flush()
    return result


def _active_edition_references(db: Session, media: PostMedia, *, exclude_edition_id: int) -> bool:
    """True when another edition or an editor lock still needs this asset."""
    from ..models import Article, ArticleMedia, Edition, EditionArticle

    rows = db.execute(
        select(ArticleMedia.id)
        .join(Article, ArticleMedia.article_id == Article.id)
        .join(EditionArticle, EditionArticle.article_id == Article.id)
        .join(Edition, EditionArticle.edition_id == Edition.id)
        .where(
            ArticleMedia.media_id == media.id,
            Edition.id != exclude_edition_id,
        )
        .where((Edition.status.notin_(["published", "archived"])) | (Article.locked.is_(True)))
        .limit(1)
    ).first()
    return rows is not None


def cleanup_published_media(db: Session, edition_id: int) -> dict:
    """Remove edition-owned working files after the published output is durable.

    Runs only from the publication transaction once the PDF, e-paper bundle,
    manifest and checksums are written and the release state is committed. The
    operation is idempotent: already-deleted files are skipped on a retry.
    Files still referenced by another active edition, or whose deletion status
    is uncertain, are protected and reported. An edition whose published bundle
    is incomplete keeps all of its media for recovery.
    """
    from ..models import Edition

    rows = list(db.scalars(select(PostMedia).where(PostMedia.edition_id == edition_id)))
    result = {
        "deleted": 0,
        "bytes": 0,
        "failed": 0,
        "protected": 0,
        "pending": 0,
    }
    edition = db.get(Edition, edition_id)
    if edition is None or edition.status not in {"published", "archived"} or not _edition_output_complete(edition):
        # Never delete working media when the published artefacts cannot be
        # verified on disk. The edition remains published; cleanup is pending.
        result["protected"] = len(rows)
        return result
    for media in rows:
        if _active_edition_references(db, media, exclude_edition_id=edition_id):
            result["protected"] += 1
            continue
        remaining = False
        for attribute in ("original_path", "derivative_path"):
            raw_path = getattr(media, attribute)
            path = _safe_cache_file(raw_path)
            if path is None:
                if raw_path:
                    result["failed"] += 1
                    remaining = True
                continue
            try:
                result["bytes"] += path.stat().st_size
                path.unlink()
                result["deleted"] += 1
                setattr(media, attribute, "")
            except OSError:
                result["failed"] += 1
                remaining = True
        if remaining:
            # Cleanup is pending, not a publication failure: a retry resumes.
            result["pending"] += 1
            continue
        media.deleted_at = utcnow()
        media.retention_state = "published_provenance_only"
    db.flush()
    return result


def rank_media(db: Session, story: StoryCluster) -> list[MediaChoice]:
    posts = _story_posts(db, story)
    if not posts:
        return []
    media_rows = list(db.scalars(select(PostMedia).where(PostMedia.post_id.in_([p.id for p in posts]))))
    text = " ".join((p.text or "") for p in posts) + " " + story.canonical_title
    story_tokens = set(text.lower().split())
    choices: list[MediaChoice] = []
    for media in media_rows:
        reasons: list[str] = []
        score = 0.0
        if media.original_path and Path(media.original_path).exists():
            metrics = image_metrics(Path(media.original_path)) if media.kind in {"photo", "image"} else {}
            if metrics:
                megapixels = metrics["width"] * metrics["height"] / 1_000_000
                score += min(megapixels / 4.0, 4.0)
                score += min(metrics["sharpness"] / 500.0, 2.0)
                if 1.2 <= metrics["aspect"] <= 1.8:
                    score += 0.8
                    reasons.append("print-friendly aspect ratio")
                if metrics["brightness"] < 15 or metrics["brightness"] > 245:
                    score -= 3.0
                    reasons.append("poor exposure")
            else:
                score += 1.0
        caption_tokens = set((media.caption or "").lower().split())
        overlap = len(caption_tokens & story_tokens)
        if overlap:
            score += min(overlap * 0.15, 1.2)
            reasons.append("caption overlaps story evidence")
        if media.is_sensitive:
            score -= 4.0
            reasons.append("sensitive imagery requires review")
        if media.is_documentary:
            score += 1.0
            reasons.append("documentary source media")
        else:
            reasons.append("labelled fixture/non-documentary media")
        if media.kind in {"video", "animation"}:
            score -= 0.5
            reasons.append("video requires a selected still")
        choices.append(MediaChoice(media=media, score=round(score, 3), reasons=reasons))
    choices.sort(key=lambda choice: choice.score, reverse=True)
    return choices


def select_lead_media(
    db: Session,
    story: StoryCluster,
    article_id: int,
    target_dir: Path,
    *,
    exclude_media_ids: set[int] | None = None,
    download_missing: bool = False,
) -> MediaChoice | None:
    from ..models import ArticleMedia

    if download_missing:
        ensure_media_files(db, story)
    excluded = exclude_media_ids or set()
    choices = [choice for choice in rank_media(db, story) if choice.media.id not in excluded]
    for choice in choices:
        media = choice.media
        if media.kind in {"video", "animation"}:
            frames = extract_video_frames(media)
            if not frames:
                continue
            frame = frames[0]
            derivative = make_derivative(
                Path(frame["path"]), target_dir, f"article_{article_id}_video_still.jpg", crop_ratio=1.5
            )
            media.derivative_path = str(derivative)
            record = ArticleMedia(
                article_id=article_id,
                media_id=media.id,
                role="lead",
                caption=(media.caption or "Video still from source video.") + f" Still at {frame['timestamp_label']}.",
                credit=media.credit or "Source channel",
                path=str(derivative),
                checksum=_checksum(derivative),
                graphic_kind="video_still",
                frame_seconds=frame["seconds"],
                selected=True,
            )
            db.add(record)
            choice.is_video_frame = True
            choice.frame_seconds = frame["seconds"]
            db.flush()
            return choice
        if not media.original_path or not Path(media.original_path).exists():
            continue
        derivative = make_derivative(
            Path(media.original_path), target_dir, f"article_{article_id}_lead.jpg", crop_ratio=1.5
        )
        media.derivative_path = str(derivative)
        record = ArticleMedia(
            article_id=article_id,
            media_id=media.id,
            role="lead",
            caption=media.caption or "Source media.",
            credit=media.credit or "Source channel",
            path=str(derivative),
            checksum=_checksum(derivative),
            graphic_kind="photo",
            selected=True,
        )
        db.add(record)
        db.flush()
        return choice
    return None


def extract_video_frames(media: PostMedia, max_frames: int = 3) -> list[dict]:
    """Deterministic scene-aware frame selection using OpenCV.

    Rejects black, flat and duplicate frames. Records the exact timestamp and
    checksum so the printed still can be traced to the source video.
    """
    if not media.original_path or not Path(media.original_path).exists():
        return []
    try:
        import cv2
    except ImportError:
        return []
    capture = cv2.VideoCapture(str(media.original_path))
    if not capture.isOpened():
        capture.release()
        return []
    fps = capture.get(cv2.CAP_PROP_FPS) or 12.0
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    if total <= 0:
        capture.release()
        return []
    sample_count = min(24, total)
    indices = sorted({int(total * i / sample_count) for i in range(sample_count)})
    candidates: list[dict] = []
    previous_hist = None
    for index in indices:
        capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = capture.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness = float(gray.mean())
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if brightness < 12 or brightness > 245 or sharpness < 8:
            continue
        hist = cv2.calcHist([gray], [0], None, [32], [0, 256])
        cv2.normalize(hist, hist)
        scene_change = 0.0
        if previous_hist is not None:
            scene_change = float(cv2.compareHist(previous_hist, hist, cv2.HISTCMP_BHATTACHARYYA))
        previous_hist = hist
        candidates.append(
            {
                "index": index,
                "seconds": round(index / fps, 2),
                "timestamp_label": _timestamp(index / fps),
                "brightness": round(brightness, 1),
                "sharpness": round(sharpness, 1),
                "scene_change": round(scene_change, 3),
                "frame": frame.copy(),
            }
        )
    capture.release()
    if not candidates:
        return []
    candidates.sort(key=lambda item: (item["sharpness"] * 0.6 + item["scene_change"] * 100), reverse=True)
    selected: list[dict] = []
    frame_dir = Path(settings.media_dir) / "video_frames" / str(media.post_id)
    frame_dir.mkdir(parents=True, exist_ok=True)
    for candidate in candidates:
        if any(abs(candidate["seconds"] - other["seconds"]) < 0.35 for other in selected):
            continue
        path = frame_dir / f"frame_{int(candidate['seconds'] * 1000):06d}.jpg"
        cv2.imwrite(str(path), candidate["frame"], [cv2.IMWRITE_JPEG_QUALITY, 92])
        selected.append(
            {
                "path": str(path),
                "seconds": candidate["seconds"],
                "timestamp_label": candidate["timestamp_label"],
                "sharpness": candidate["sharpness"],
                "scene_change": candidate["scene_change"],
                "sha256": _checksum(path),
            }
        )
        if len(selected) >= max_frames:
            break
    return selected


def _timestamp(seconds: float) -> str:
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:04.1f}"


def make_collage(images: list[dict], target: Path, width: int = 1600) -> Path:
    """Create a labelled collage with separable boundaries and credits."""
    if not images:
        raise ValueError("At least one image is required.")
    columns = min(len(images), 3)
    rows = math.ceil(len(images) / columns)
    gutter = 12
    cell_width = (width - gutter * (columns + 1)) // columns
    cell_height = int(cell_width * 2 / 3)
    height = rows * cell_height + gutter * (rows + 1) + 90
    canvas = Image.new("RGB", (width, height), "#efece4")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
    for index, item in enumerate(images):
        row, column = divmod(index, columns)
        with Image.open(item["path"]) as image:
            image = ImageOps.fit(image.convert("RGB"), (cell_width, cell_height), Image.Resampling.LANCZOS)
            x = gutter + column * (cell_width + gutter)
            y = gutter + row * (cell_height + gutter)
            canvas.paste(image, (x, y))
            draw.rectangle([x, y, x + cell_width, y + cell_height], outline=(40, 40, 38), width=2)
            label = item.get("credit") or ""
            if label:
                draw.text((x + 8, y + cell_height - 28), label[:70], font=font, fill=(245, 243, 237))
    note = "Composite of separate source images; captions and credits apply individually."
    draw.text((gutter, height - 56), note, font=font, fill=(30, 30, 28))
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, "JPEG", quality=92)
    return target
