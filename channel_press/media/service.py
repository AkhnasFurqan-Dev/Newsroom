from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import PostFragment, PostMedia, SourcePost, StoryCluster
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
) -> MediaChoice | None:
    from ..models import ArticleMedia

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
            record = ArticleMedia(
                article_id=article_id,
                media_id=media.id,
                role="lead",
                caption=(media.caption or "Video still from source video.") + f" Still at {frame['timestamp_label']}.",
                credit=media.credit or "Source channel",
                path=str(derivative),
                checksum=_checksum(derivative),
                graphic_kind="video_still",
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
