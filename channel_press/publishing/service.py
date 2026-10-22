from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import BASE_DIR, settings
from ..layout.engine import RenderResult, compile_edition
from ..layout.planner import EditionPlan, create_plan
from ..models import (
    Article,
    Edition,
    EditionArticle,
    EditionAsset,
    EditionPage,
    EditionSchedule,
    EditionWatermark,
    PublicationAttempt,
    PublicationProfile,
    ReviewFlag,
    SourceAppendix,
)
from ..timeutil import human_local, iso_utc, utcnow

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

ASSET_KINDS = {
    "edition.pdf": "pdf",
    "edition.html": "print_html",
}


class PublicationError(RuntimeError):
    pass


class LayoutQualityError(PublicationError):
    def __init__(self, warnings: list[str]):
        self.warnings = warnings
        super().__init__("Layout quality checks failed: " + "; ".join(warnings[:6]))


@dataclass
class PublishResult:
    edition_id: int
    status: str
    output_dir: Path
    pdf_path: Path
    epaper_dir: Path
    manifest_path: Path
    warnings: list[str]
    version: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def edition_slug(profile: PublicationProfile) -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "-" for ch in profile.name)
    return "-".join(part for part in raw.split("-") if part) or "edition"


def edition_folder_name(edition: Edition, profile: PublicationProfile) -> str:
    stamp = edition.window_end.strftime("%Y-%m-%d")
    return f"{stamp}-{edition.kind}-{edition.issue_number:03d}-v{edition.version}"


def _copy_asset(source: Path, target: Path) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return {"path": target.name, "bytes": target.stat().st_size, "sha256": _sha256(target)}


def _write_epaper(
    *,
    db: Session,
    edition: Edition,
    plan: EditionPlan,
    render: RenderResult,
    target: Path,
    demo_edition: bool,
) -> dict:
    from PIL import Image

    pages_dir = target / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    page_payload: list[dict] = []
    story_pages = {str(item["story_id"]): item["pages"] for item in render.meta.get("story_pages", [])}
    for index, preview in enumerate(render.preview_paths, start=1):
        web_path = pages_dir / f"page-{index:02d}.jpg"
        with Image.open(preview) as image:
            image.convert("RGB").save(web_path, "JPEG", quality=86, optimize=True)
        meta_page = next((p for p in render.pages if p.get("page") == index), {})
        page_payload.append(
            {
                "number": index,
                "image": f"pages/{web_path.name}",
                "template": meta_page.get("template", ""),
                "section": meta_page.get("section", ""),
                "stories": meta_page.get("stories", []),
                "chars": meta_page.get("chars", 0),
            }
        )
    articles = []
    for package in plan.packages:
        pages = story_pages.get(str(package.story.id), [])
        page_numbers = sorted({item["page"] for item in pages})
        articles.append(
            {
                "title": package.article.title,
                "deck": package.article.deck,
                "standfirst": package.article.standfirst,
                "genre": package.genre,
                "section": package.section,
                "paragraphs": package.body_paragraphs,
                "pages": page_numbers,
                "source_refs": package.source_refs,
                "correction_note": package.correction_note,
                "audit": json.loads(package.article.audit_json or "{}"),
            }
        )
    evidence = {
        "generated_at": iso_utc(utcnow()),
        "window_start": iso_utc(edition.window_start),
        "window_end": iso_utc(edition.window_end),
        "cutoff_at": iso_utc(edition.cutoff_at),
        "timezone": edition.timezone,
        "page_count": render.qa.get("page_count", 0),
        "pdf_sha256": _sha256(render.pdf_path),
        "pdf_bytes": render.pdf_path.stat().st_size,
        "qa": render.qa,
        "cartographic_convention": plan.profile.map_profile,
        "demo_edition": demo_edition,
    }
    html = _environment().get_template("epaper.html.j2").render(
        edition=edition,
        profile=plan.profile,
        pages=page_payload,
        articles=articles,
        appendix_entries=plan.appendix_entries,
        evidence=evidence,
        demo_edition=demo_edition,
        edition_date=human_local(edition.window_end, edition.timezone, "%d %B %Y"),
    )
    (target / "index.html").write_text(html, encoding="utf-8")
    text_html = _environment().get_template("epaper_text.html.j2").render(
        edition=edition,
        profile=plan.profile,
        articles=articles,
        appendix_entries=plan.appendix_entries,
        demo_edition=demo_edition,
        edition_date=human_local(edition.window_end, edition.timezone, "%d %B %Y"),
    )
    (target / "text.html").write_text(text_html, encoding="utf-8")
    (target / "assets").mkdir(exist_ok=True)
    (target / "assets" / "epaper.css").write_text(
        (TEMPLATE_DIR / "epaper.css").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (target / "assets" / "epaper.js").write_text(
        (TEMPLATE_DIR / "epaper.js").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "channel_press.epaper.v1",
                "edition": {
                    "id": edition.id,
                    "title": edition.title,
                    "kind": edition.kind,
                    "issue_number": edition.issue_number,
                    "volume": edition.volume,
                    "version": edition.version,
                },
                "publication": plan.profile.name,
                "pages": page_payload,
                "articles": articles,
                "sources": plan.appendix_entries,
                "evidence": evidence,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "pages": page_payload,
        "articles": articles,
        "evidence": evidence,
    }


def _persist_pages(
    db: Session,
    edition: Edition,
    plan: EditionPlan,
    render: RenderResult,
    *,
    preview_root: Path | None = None,
) -> None:
    db.execute(EditionPage.__table__.delete().where(EditionPage.edition_id == edition.id))
    for meta in render.pages:
        preview = ""
        number = int(meta.get("page") or 0)
        if 0 < number <= len(render.preview_paths):
            source = render.preview_paths[number - 1]
            if preview_root is not None:
                source = preview_root / source.name
            preview = str(source)
        db.add(
            EditionPage(
                edition_id=edition.id,
                page_number=number,
                template=str(meta.get("template", "")),
                html="",
                qa_json=json.dumps(
                    {
                        "blank": meta.get("blank"),
                        "min_font_pt": meta.get("min_font_pt"),
                        "overflow": meta.get("overflow", []),
                        "chars": meta.get("chars"),
                    },
                    ensure_ascii=False,
                ),
                preview_path=preview,
                story_map_json=json.dumps(meta.get("stories", []), ensure_ascii=False),
            )
        )
    db.execute(SourceAppendix.__table__.delete().where(SourceAppendix.edition_id == edition.id))
    db.add(
        SourceAppendix(
            edition_id=edition.id,
            page_number=int(render.meta.get("appendix_page") or 0),
            title="Sources & provenance",
            content_html="",
            entries_json=json.dumps(plan.appendix_entries, ensure_ascii=False),
        )
    )
    db.flush()


def edition_review_blockers(db: Session, edition_id: int) -> list[str]:
    """Return blockers that belong to this edition's own article set.

    Older code considered every open review flag in the database, which meant a
    flag from a previous edition could permanently prevent an unrelated clean
    edition from being published. Scope both checks to the edition.
    """
    from sqlalchemy import or_

    blockers: list[str] = []
    article_ids = list(
        db.scalars(select(EditionArticle.article_id).where(EditionArticle.edition_id == edition_id))
    )
    story_ids: list[int] = []
    if article_ids:
        story_ids = list(
            db.scalars(select(Article.story_id).where(Article.id.in_(article_ids)))
        )
    conditions = []
    if article_ids:
        conditions.append(ReviewFlag.article_id.in_(article_ids))
    if story_ids:
        conditions.append(ReviewFlag.story_id.in_(story_ids))
    flags = (
        list(
            db.scalars(
                select(ReviewFlag).where(
                    ReviewFlag.status == "open",
                    ReviewFlag.severity.in_(["block_publish", "review"]),
                    or_(*conditions),
                )
            )
        )
        if conditions
        else []
    )
    page_exists = db.scalar(
        select(EditionPage.id).where(EditionPage.edition_id == edition_id).limit(1)
    )
    if not page_exists:
        blockers.append("Layout has not been generated.")
    return blockers + [
        f"Open review item: {flag.category} — {flag.message[:120]}" for flag in flags
    ]


def record_manifest_release(edition: Edition) -> None:
    """Keep the on-disk manifest and checksums truthful when release happens later.

    The manifest is written during layout, before an editor has necessarily
    released the issue. Rewriting only the release fields preserves the
    build-time evidence while avoiding a published PDF whose manifest still
    claims to be a draft.
    """
    if not edition.manifest_json:
        return
    try:
        manifest = json.loads(edition.manifest_json)
    except (TypeError, ValueError):
        return
    record = manifest.get("edition")
    if not isinstance(record, dict):
        return
    record["status"] = edition.status
    record["published_at"] = iso_utc(edition.published_at) if edition.published_at else None
    edition.manifest_json = json.dumps(manifest, ensure_ascii=False)
    if not edition.output_dir:
        return
    output = Path(edition.output_dir)
    manifest_path = output / "manifest.json"
    if not manifest_path.exists():
        return
    manifest_path.write_text(edition.manifest_json, encoding="utf-8")
    checksums_path = output / "checksums.json"
    if checksums_path.exists():
        checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
        checksums["manifest.json"] = _sha256(manifest_path)
        checksums_path.write_text(json.dumps(checksums, indent=2), encoding="utf-8")


def publish_edition(
    db: Session,
    edition: Edition,
    *,
    output_root: Path | None = None,
    demo_edition: bool | None = None,
    actor_id: int | None = None,
    allow_quality_warnings: bool = False,
    mark_published: bool = True,
) -> PublishResult:
    profile = db.get(PublicationProfile, 1) or db.scalar(select(PublicationProfile).limit(1))
    if profile is None:
        raise PublicationError("Publication profile is not configured.")
    if demo_edition is None:
        channel = edition.channel_id
        from ..models import Channel

        record = db.get(Channel, channel)
        demo_edition = bool(record and record.is_demo)
    output_root = output_root or settings.output_dir
    output_root.mkdir(parents=True, exist_ok=True)
    staging = output_root / f".staging-{edition.id}-{uuid.uuid4().hex[:10]}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        render = compile_edition(
            db,
            edition,
            output_dir=staging,
            demo_edition=demo_edition,
            media_dir=settings.media_dir,
        )
        if render.qa.get("warnings") and not allow_quality_warnings:
            raise LayoutQualityError(list(render.qa["warnings"]))
        plan = create_plan(db, edition, profile, media_dir=settings.media_dir)
        epaper_dir = staging / "epaper"
        epaper = _write_epaper(
            db=db,
            edition=edition,
            plan=plan,
            render=render,
            target=epaper_dir,
            demo_edition=demo_edition,
        )
        # Copy the print artefacts into a predictable layout.
        pdf_target = staging / "sample_issue.pdf"
        shutil.copy2(render.pdf_path, pdf_target)
        (staging / "print.html").write_text(render.html_path.read_text(encoding="utf-8"), encoding="utf-8")
        manifest = {
            "schema": "channel_press.manifest.v1",
            "edition": {
                "id": edition.id,
                "title": edition.title,
                "kind": edition.kind,
                "issue_number": edition.issue_number,
                "volume": edition.volume,
                "version": edition.version,
                "status": "published" if mark_published else "awaiting_review",
                "published_at": iso_utc(utcnow()) if mark_published else None,
                "timezone": edition.timezone,
                "window_start": iso_utc(edition.window_start),
                "window_end": iso_utc(edition.window_end),
                "cutoff_at": iso_utc(edition.cutoff_at),
                "target_release_at": iso_utc(edition.target_release_at),
            },
            "publication": {
                "name": profile.name,
                "handle": profile.channel_handle,
                "city": profile.city,
                "cartographic_profile": plan.profile.map_profile,
                "accent": profile.accent,
            },
            "generated_at": iso_utc(utcnow()),
            "demo_edition": bool(demo_edition),
            "pdf": {
                "file": "sample_issue.pdf",
                "bytes": pdf_target.stat().st_size,
                "sha256": _sha256(pdf_target),
                "page_count": render.qa.get("page_count"),
                "page_size_pt": render.qa.get("expected_size_pt"),
            },
            "quality": render.qa,
            "pages": render.pages,
            "sources": plan.appendix_entries,
            "articles": [
                {
                    "id": package.article.id,
                    "story_id": package.story.id,
                    "title": package.article.title,
                    "section": package.section,
                    "genre": package.genre,
                    "status": package.article.status,
                    "source_refs": package.source_refs,
                }
                for package in plan.packages
            ],
            "epaper": {
                "index": "epaper/index.html",
                "text": "epaper/text.html",
                "manifest": "epaper/manifest.json",
                "pages": [page["image"] for page in epaper["pages"]],
            },
            "provenance_note": (
                "Generated from the owner's authorized source channel. Synthetic fixture data only."
                if demo_edition
                else "Generated from the owner's authorized source channel."
            ),
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        checksums = {
            "sample_issue.pdf": _sha256(pdf_target),
            "manifest.json": _sha256(manifest_path),
            "epaper/index.html": _sha256(epaper_dir / "index.html"),
            "epaper/manifest.json": _sha256(epaper_dir / "manifest.json"),
        }
        (staging / "checksums.json").write_text(
            json.dumps(checksums, indent=2), encoding="utf-8"
        )

        # Validate before the atomic rename.
        if not pdf_target.exists() or pdf_target.stat().st_size < 5_000:
            raise PublicationError("PDF output is missing or implausibly small.")
        if not (epaper_dir / "index.html").exists():
            raise PublicationError("E-paper index was not generated.")
        if render.qa.get("page_count", 0) < 1:
            raise PublicationError("PDF contains no pages.")

        final_dir = output_root / edition_slug(profile) / edition_folder_name(edition, profile)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        if final_dir.exists():
            raise PublicationError(f"Output folder already exists: {final_dir}")
        staging.replace(final_dir)
        staging = final_dir
        pdf_path = final_dir / "sample_issue.pdf"
        epaper_path = final_dir / "epaper"
        manifest_path = final_dir / "manifest.json"

        _persist_pages(db, edition, plan, render, preview_root=final_dir / "pages")
        db.execute(EditionAsset.__table__.delete().where(EditionAsset.edition_id == edition.id))
        for name, kind in ASSET_KINDS.items():
            path = final_dir / name
            if path.exists():
                db.add(
                    EditionAsset(
                        edition_id=edition.id,
                        kind=kind,
                        path=str(path),
                        bytes=path.stat().st_size,
                    )
                )
        for page in epaper["pages"]:
            asset_path = epaper_path / page["image"]
            db.add(
                EditionAsset(
                    edition_id=edition.id,
                    kind="page_preview",
                    path=str(asset_path),
                    bytes=asset_path.stat().st_size,
                )
            )
        edition.output_dir = str(final_dir)
        edition.pdf_path = str(pdf_path)
        edition.epaper_dir = str(epaper_path)
        edition.manifest_json = json.dumps(manifest, ensure_ascii=False)
        edition.updated_at = utcnow()
        if mark_published:
            edition.status = "published"
            edition.published_at = utcnow()
            db.add(
                PublicationAttempt(
                    edition_id=edition.id,
                    destination=str(final_dir),
                    status="succeeded",
                    checksum=checksums["sample_issue.pdf"],
                )
            )
        else:
            edition.status = "awaiting_review"
        if mark_published:
            record_manifest_release(edition)
        db.flush()
        return PublishResult(
            edition_id=edition.id,
            status=edition.status,
            output_dir=final_dir,
            pdf_path=pdf_path,
            epaper_dir=epaper_path,
            manifest_path=manifest_path,
            warnings=list(render.qa.get("warnings", [])),
            version=edition.version,
        )
    except Exception:
        if staging.exists() and staging.name.startswith(".staging-"):
            shutil.rmtree(staging, ignore_errors=True)
        raise


def advance_watermark(db: Session, schedule: EditionSchedule, cutoff_at: datetime) -> None:
    watermark = db.scalar(
        select(EditionWatermark).where(
            EditionWatermark.channel_id == schedule.channel_id,
            EditionWatermark.kind == schedule.kind,
        )
    )
    if watermark is None:
        db.add(
            EditionWatermark(
                channel_id=schedule.channel_id,
                kind=schedule.kind,
                last_cutoff_at=cutoff_at,
            )
        )
    elif watermark.last_cutoff_at < cutoff_at:
        watermark.last_cutoff_at = cutoff_at
        watermark.updated_at = utcnow()
    db.flush()
