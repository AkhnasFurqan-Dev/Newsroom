from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from html import escape as html_escape
from typing import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..layout.engine import RenderResult, compile_edition, edition_display_name
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
    PostMedia,
    ReviewFlag,
    SourceAppendix,
)
from ..publications import publication_for_edition
from ..timeutil import human_local, iso_utc, utcnow

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"

ASSET_KINDS = {
    "edition.pdf": "pdf",
    "edition.html": "print_html",
}


def manifest_sources(entries: list[dict]) -> list[dict]:
    """Digital provenance without embedding the QR image bytes in JSON."""
    return [
        {key: value for key, value in entry.items() if key != "qr_data_uri"}
        for entry in entries
    ]


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
    media_cleanup: dict = field(default_factory=dict)


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


def _write_evidence_bundles(
    *,
    target: Path,
    edition: Edition,
    plan: EditionPlan,
    articles: list[dict],
    edition_date: str,
    edition_name: str,
    demo_edition: bool,
) -> None:
    """Write one small static evidence page per printed report.

    The page is a plain folder of HTML so an owner can deploy the exported
    e-paper bundle to any static host. A QR is only pointed at it when the
    publication has an explicitly verified public base URL (see
    :func:`channel_press.layout.engine._attach_story_qr`).
    """
    root = target / "sources"
    root.mkdir(parents=True, exist_ok=True)
    template = _environment().get_template("evidence.html.j2")
    for article in articles:
        public_id = article.get("public_id") or ""
        if not public_id:
            continue
        folder = root / public_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(
            template.render(
                profile=plan.profile,
                edition=edition,
                edition_name=edition_name,
                edition_date=edition_date,
                article=article,
                demo_edition=demo_edition,
            ),
            encoding="utf-8",
        )
    index_rows = "".join(
        f'<li><a href="{html_escape(str(article.get("public_id")))}/index.html">'
        f'{html_escape(str(article.get("title") or "Report"))}</a></li>'
        for article in articles
        if article.get("public_id")
    )
    (root / "index.html").write_text(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>Sources — {html_escape(edition_name)}</title></head>"
        "<body style=\"font-family:Georgia,serif;max-width:44rem;margin:2rem auto;padding:0 1rem\">"
        f"<h1>{html_escape(plan.profile.name)}</h1>"
        f"<p>{html_escape(edition_name)} · {html_escape(edition_date)}</p>"
        f"<ul>{index_rows}</ul></body></html>",
        encoding="utf-8",
    )


def _write_epaper(
    *,
    db: Session,
    edition: Edition,
    plan: EditionPlan,
    render: RenderResult,
    target: Path,
    demo_edition: bool,
    theme=None,
) -> dict:
    from PIL import Image

    from ..theming import available_font_assets, compile_theme, theme_css

    if theme is None:
        theme = compile_theme(db, plan.profile, edition, font_dir=target / "assets")
    epaper_css, _warnings = theme_css(
        theme.payload,
        assets=available_font_assets(db),
        font_dir=target / "assets",
    )
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
    sources_by_article: dict[int, list[dict]] = {}
    for entry in plan.appendix_entries:
        article_id = entry.get("article_id")
        if article_id is not None:
            sources_by_article.setdefault(int(article_id), []).append(entry)
    for package in plan.packages:
        pages = story_pages.get(str(package.story.id), [])
        page_numbers = sorted({item["page"] for item in pages})
        articles.append(
            {
                "title": package.headline or package.article.title,
                "deck": package.deck,
                "standfirst": package.standfirst,
                "genre": package.genre,
                "section": package.section,
                "paragraphs": package.body_paragraphs,
                "pages": page_numbers,
                "public_id": package.public_id,
                "source_refs": package.source_refs,
                "sources": sources_by_article.get(package.article.id, []),
                "qr_status": package.qr_status,
                "qr_target": package.qr_target,
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
        "theme": theme.hash,
    }
    edition_date = human_local(edition.window_end, edition.timezone, "%d %B %Y")
    edition_name = edition_display_name(edition)
    html = _environment().get_template("epaper.html.j2").render(
        edition=edition,
        profile=plan.profile,
        pages=page_payload,
        articles=articles,
        evidence=evidence,
        demo_edition=demo_edition,
        edition_date=edition_date,
        edition_name=edition_name,
    )
    (target / "index.html").write_text(html, encoding="utf-8")
    text_html = _environment().get_template("epaper_text.html.j2").render(
        edition=edition,
        profile=plan.profile,
        articles=articles,
        demo_edition=demo_edition,
        edition_date=edition_date,
        edition_name=edition_name,
    )
    (target / "text.html").write_text(text_html, encoding="utf-8")
    _write_evidence_bundles(
        target=target,
        edition=edition,
        plan=plan,
        articles=articles,
        edition_date=edition_date,
        edition_name=edition_name,
        demo_edition=demo_edition,
    )
    (target / "assets").mkdir(exist_ok=True)
    (target / "assets" / "epaper.css").write_text(
        (TEMPLATE_DIR / "epaper.css").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (target / "assets" / "theme.css").write_text(epaper_css, encoding="utf-8")
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
                    "display_name": edition_display_name(edition),
                    "kind": edition.kind,
                    "issue_number": edition.issue_number,
                    "volume": edition.volume,
                    "version": edition.version,
                },
                "publication": plan.profile.name,
                "pages": page_payload,
                "articles": articles,
                "sources": plan.appendix_entries,
                "qr": [
                    {
                        "article_id": package.article.id,
                        "public_id": package.public_id,
                        "status": package.qr_status,
                        "target": package.qr_target,
                        "reason": package.qr_reason,
                    }
                    for package in plan.packages
                ],
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
            page_number=0,
            title="Internal source record",
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


def record_media_cleanup(edition: Edition, result: dict) -> None:
    """Persist the cleanup outcome in the edition manifest without hiding failures.

    A non-zero ``pending`` value means the publication is still valid but a
    retry is required; it is never reported as a failed publication.
    """
    if not edition.manifest_json:
        return
    try:
        manifest = json.loads(edition.manifest_json)
    except (TypeError, ValueError):
        return
    manifest["media_cleanup"] = {
        key: result.get(key, 0)
        for key in ("deleted", "bytes", "failed", "protected", "pending")
    }
    manifest["media_cleanup"]["at"] = iso_utc(utcnow())
    edition.manifest_json = json.dumps(manifest, ensure_ascii=False)
    if not edition.output_dir:
        return
    path = Path(edition.output_dir) / "manifest.json"
    if not path.exists():
        return
    path.write_text(edition.manifest_json, encoding="utf-8")
    # ``checksums.json`` covers the manifest, so refreshing the manifest after
    # publication must refresh its recorded digest too. Otherwise a correct
    # publication would fail its own integrity check.
    checksums_path = path.parent / "checksums.json"
    if checksums_path.exists():
        try:
            checksums = json.loads(checksums_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        checksums["manifest.json"] = _sha256(path)
        checksums_path.write_text(json.dumps(checksums, indent=2), encoding="utf-8")


def finalize_media_cleanup(db: Session, edition: Edition, *, actor=None) -> dict:
    """Run the idempotent post-publication cleanup and record its outcome.

    Only call this after the PDF, e-paper bundle, manifest and checksums are
    written and the published state is durable. A cleanup failure is recorded as
    pending for a retry; it must never turn a successful publication into a
    failed one.
    """
    from ..auth import audit
    from ..media.service import cleanup_published_media

    # File deletion cannot be rolled back. Make the release durable first, so a
    # later transaction failure can never leave a published edition pointing at
    # media that was already removed.
    db.commit()
    result = cleanup_published_media(db, edition.id)
    record_media_cleanup(edition, result)
    audit(
        db,
        actor,
        "edition.media_cleanup",
        "edition",
        edition.id,
        {key: result.get(key, 0) for key in ("deleted", "bytes", "failed", "protected", "pending")},
    )
    db.commit()
    return result


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
    layout_progress: Callable[[str, str], None] | None = None,
    preview: bool = False,
) -> PublishResult:
    """Render an edition.

    ``preview=True`` writes a private working preview and never mutates the
    publication fields (``output_dir``/``pdf_path``/``status``). A preview is
    therefore never a published version, even when the edition itself is
    published: the caller edits a working revision instead.
    """
    profile = publication_for_edition(db, edition)
    if profile is None:
        raise PublicationError("Publication profile is not configured.")
    if preview:
        mark_published = False
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
            progress=layout_progress,
        )
        if render.qa.get("warnings") and not allow_quality_warnings:
            raise LayoutQualityError(list(render.qa["warnings"]))
        # Attribution audit: the publication/source channel must never be
        # written as an individual reporter or eyewitness, and no staff writer
        # may be invented. Scans the exact text that becomes the PDF and the
        # e-paper transcript before anything can be released.
        from ..editorial.moderation import attribution_issues

        scan_text = re.sub(r"<[^>]+>", " ", render.html_path.read_text(encoding="utf-8"))
        attribution_findings: set[str] = set()
        for name in {profile.name.strip(), (profile.channel_handle or "").strip()}:
            if name:
                attribution_findings.update(attribution_issues(scan_text, name))
        if attribution_findings:
            render.qa.setdefault("warnings", []).extend(
                f"attribution: {issue}" for issue in sorted(attribution_findings)
            )
            if not allow_quality_warnings:
                raise LayoutQualityError(list(render.qa["warnings"]))
        plan = render.plan or create_plan(
            db,
            edition,
            profile,
            media_dir=settings.media_dir,
            download_media=False,
        )
        from ..theming import available_font_assets, pin_edition_theme, theme_manifest

        theme = render.theme
        if theme is None:
            from ..theming import compile_theme

            theme = compile_theme(db, profile, edition)
        if not preview:
            # Freeze the exact colours, fonts and metrics used for this release
            # before any archived issue can be rewritten by a later theme save.
            pin_edition_theme(db, edition, profile, theme)
        epaper_dir = staging / "epaper"
        if layout_progress:
            layout_progress("layout_generate_epaper", "Generating the e-paper bundle")
        epaper = _write_epaper(
            db=db,
            edition=edition,
            plan=plan,
            render=render,
            target=epaper_dir,
            demo_edition=demo_edition,
            theme=theme,
        )
        for package in plan.packages:
            if package.media_id is not None:
                media = db.get(PostMedia, package.media_id)
                if media is not None and media.download_state == "downloaded":
                    # Only fetched source media is an edition working file. An
                    # owner-supplied local import may be the only copy and is
                    # cleared only by an explicit cache command.
                    media.edition_id = edition.id
                    media.retention_state = "edition_working_media"
        from ..editorial.revisions import (
            article_snapshot,
            edition_content_fingerprint,
            freeze_edition_articles,
            snapshot_hash,
        )

        if not preview:
            # The rendered bundle and its frozen article snapshots become the
            # immutable record of this version. Do this before the manifest so
            # the recorded revision/hash is exactly what was rendered.
            freeze_edition_articles(db, edition)
        # Copy the print artefacts into a predictable layout.
        pdf_target = staging / "sample_issue.pdf"
        shutil.copy2(render.pdf_path, pdf_target)
        (staging / "print.html").write_text(render.html_path.read_text(encoding="utf-8"), encoding="utf-8")
        manifest_articles = []
        for package in plan.packages:
            snapshot = article_snapshot(db, package.article)
            manifest_articles.append(
                {
                    "id": package.article.id,
                    "story_id": package.story.id,
                    "title": package.article.title,
                    "section": package.section,
                    "genre": package.genre,
                    "status": package.article.status,
                    "revision": package.article.revision,
                    "content_sha256": snapshot_hash(snapshot),
                    "source_refs": package.source_refs,
                }
            )
        manifest = {
            "schema": "channel_press.manifest.v1",
            "edition": {
                "id": edition.id,
                "title": edition.title,
                "display_name": edition_display_name(edition),
                "kind": edition.kind,
                "issue_number": edition.issue_number,
                "volume": edition.volume,
                "version": edition.version,
                "parent_edition_id": edition.parent_edition_id,
                "status": (
                    "preview" if preview else ("published" if mark_published else "awaiting_review")
                ),
                "published_at": (
                    None if preview else (iso_utc(utcnow()) if mark_published else None)
                ),
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
                "accent": theme.payload["colors"]["accent"],
            },
            "theme": theme_manifest(
                theme.theme,
                theme.payload,
                available_font_assets(db),
            ),
            "generated_at": iso_utc(utcnow()),
            "preview": preview,
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
            "sources": manifest_sources(plan.appendix_entries),
            "qr": [
                {
                    "article_id": package.article.id,
                    "public_id": package.public_id,
                    "status": package.qr_status,
                    "target": package.qr_target,
                    "reason": package.qr_reason,
                }
                for package in plan.packages
            ],
            "articles": manifest_articles,
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

        if preview:
            final_dir = output_root / edition_slug(profile) / f".preview-{edition.id}"
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            # The preview folder is owned by this edition and can be replaced:
            # it is never a published artefact.
            if final_dir.exists():
                shutil.rmtree(final_dir)
        else:
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
        if preview:
            edition.preview_dir = str(final_dir)
            edition.preview_pdf_path = str(pdf_path)
            edition.preview_revision = edition_content_fingerprint(db, edition)
            edition.preview_theme_hash = theme.hash
            edition.preview_updated_at = utcnow()
            edition.preview_state = "current"
            edition.updated_at = utcnow()
            db.flush()
            return PublishResult(
                edition_id=edition.id,
                status="preview",
                output_dir=final_dir,
                pdf_path=pdf_path,
                epaper_dir=epaper_path,
                manifest_path=manifest_path,
                warnings=list(render.qa.get("warnings", [])),
                version=edition.version,
                media_cleanup={},
            )
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
            # The published PDF and e-paper bundle are self-contained; only now
            # is the edition's working media deleted. Cleanup is a separate,
            # idempotent step: a failure here is reported as pending and never
            # turns a successful publication into a failed one.
            if layout_progress:
                layout_progress("clean_media", "Publication durable; cleaning edition working media")
            media_cleanup = finalize_media_cleanup(db, edition)
            if layout_progress:
                layout_progress(
                    "clean_media",
                    f"Media cleanup: {media_cleanup['deleted']} files, "
                    f"{media_cleanup['bytes']} bytes freed"
                    + (f", {media_cleanup['pending']} pending" if media_cleanup.get("pending") else "")
                    + (f", {media_cleanup['failed']} failed" if media_cleanup.get("failed") else ""),
                )
        else:
            media_cleanup = {}
        if mark_published:
            # The release bundle is now the authoritative rendered output. The
            # private preview folder is redundant and must not be mistaken for
            # the published artefact.
            _discard_preview(edition, output_root)
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
            media_cleanup=media_cleanup,
        )
    except Exception:
        if staging.exists() and staging.name.startswith(".staging-"):
            shutil.rmtree(staging, ignore_errors=True)
        raise


def _discard_preview(edition: Edition, output_root: Path) -> None:
    """Forget and delete the working preview once a version is released."""
    preview_dir = edition.preview_dir
    edition.preview_dir = ""
    edition.preview_pdf_path = ""
    edition.preview_state = "current"
    edition.preview_updated_at = utcnow()
    if not preview_dir:
        return
    try:
        target = Path(preview_dir).resolve()
        root = output_root.resolve()
    except OSError:
        return
    if root in target.parents and target.name.startswith(".preview-"):
        shutil.rmtree(target, ignore_errors=True)



def render_preview(
    db: Session,
    edition: Edition,
    *,
    actor_id: int | None = None,
) -> PublishResult:
    """Render the working preview without publishing.

    Quality warnings are recorded rather than raised: an editor must still see
    the page that needs fixing. Publication keeps the strict gate.
    """
    result = publish_edition(
        db,
        edition,
        actor_id=actor_id,
        mark_published=False,
        allow_quality_warnings=True,
        preview=True,
    )
    audit_note = {
        "preview_revision": edition.preview_revision,
        "pages": len(result.warnings),
    }
    _ = audit_note
    return result


def purge_unpublished_output(edition: Edition, *, output_root: Path | None = None) -> None:
    """Remove a stale draft bundle before re-rendering it for publication.

    Only an unpublished edition may be cleaned; a published folder is immutable.
    The check is deliberately path-based and conservative.
    """
    if edition.status == "published" or not edition.output_dir:
        return
    root = (output_root or settings.output_dir).resolve()
    try:
        target = Path(edition.output_dir).resolve()
    except OSError:
        return
    if root not in target.parents:
        return
    if target.name.startswith(".preview-") or target.name.startswith(".staging-"):
        return
    shutil.rmtree(target, ignore_errors=True)


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
