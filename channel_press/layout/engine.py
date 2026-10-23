from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from pathlib import Path

import fitz
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup
from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..constants import SECTION_LABELS
from ..models import Edition
from ..timeutil import human_local
from .planner import EditionPlan, StoryPackage, create_plan

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
VENDOR_DIR = BASE_DIR / "channel_press" / "web" / "static" / "vendor"
CSS_PATH = BASE_DIR / "channel_press" / "web" / "static" / "css" / "print.css"

SECTION_TEMPLATES = {
    "conflict": "conflict_visual",
    "analysis": "analysis_feature",
    "opinion": "opinion",
    "briefs": "briefs",
    "national": "national",
    "regional": "regional",
    "international": "international",
    "defence": "defence",
}

GENRE_LABELS = {
    "news": "News",
    "analysis": "Analysis",
    "opinion": "Opinion",
    "editor_note": "Editor's note",
    "brief": "Brief",
}

KIND_LABELS = {
    "daily": "Daily edition",
    "weekly_roundup": "Weekly roundup",
    "monthly_roundup": "Monthly roundup",
    "special": "Special edition",
    "manual": "Manual edition",
}


@dataclass
class StoryChunk:
    package: StoryPackage
    index: int
    count: int
    paragraphs: list[str]


@dataclass
class RenderResult:
    html_path: Path
    pdf_path: Path
    preview_paths: list[Path]
    pages: list[dict]
    qa: dict
    meta: dict


@dataclass
class PageChunkPlan:
    lead: StoryPackage
    lead_chunks: list[StoryChunk]
    front_briefs: list[StoryPackage]
    front_digest: list[StoryPackage]
    sections: list[tuple[str, list[StoryPackage]]]
    section_chunks: dict[str, list[StoryChunk]]
    templates: dict[str, str] = field(default_factory=dict)
    section_break: dict[str, bool] = field(default_factory=dict)
    pair_sections: set[str] = field(default_factory=set)
    # Multi-story sections are laid out two reports to a row so a section with
    # four reports fills a tabloid page instead of leaving most of it blank.
    section_rows: dict[str, list[list[StoryChunk]]] = field(default_factory=dict)


def _words(paragraphs: list[str]) -> int:
    return sum(len(p.split()) for p in paragraphs)


def chunk_paragraphs(
    package: StoryPackage,
    *,
    max_words: int,
    first_max_words: int | None = None,
) -> list[StoryChunk]:
    """Split a story at paragraph boundaries for deterministic pagination.

    Chunks share the story/article identity, so page references can be added
    after layout and validated against the actual rendered pages.
    """
    paragraphs = [p.strip() for p in package.body_paragraphs if p.strip()]
    if not paragraphs:
        paragraphs = [package.article.standfirst or package.article.title]
    limit = first_max_words or max_words
    groups: list[list[str]] = []
    current: list[str] = []
    for paragraph in paragraphs:
        candidate = current + [paragraph]
        if current and _words(candidate) > limit:
            groups.append(current)
            current = [paragraph]
            limit = max_words
        else:
            current = candidate
    if current:
        groups.append(current)
    if not groups:
        groups = [paragraphs]
    # A one-line tail reads badly; move it back into the previous chunk when
    # the combined chunk remains within a small tolerance.
    if len(groups) > 1 and _words(groups[-1]) < 35 and _words(groups[-2] + groups[-1]) <= max_words * 1.12:
        groups[-2] = groups[-2] + groups[-1]
        groups.pop()
    return [
        StoryChunk(package=package, index=index + 1, count=len(groups), paragraphs=group)
        for index, group in enumerate(groups)
    ]


def _max_words(package: StoryPackage, *, front: bool = False) -> int:
    if package.is_brief:
        return 400
    if front:
        return 310 if (package.media_path or package.map_result) else 380
    if package.section == "analysis":
        return 580
    if package.section == "opinion":
        return 500
    if package.section == "conflict":
        return 430
    return 470


def build_page_plan(plan: EditionPlan) -> PageChunkPlan:
    lead = plan.lead
    lead_chunks = chunk_paragraphs(
        lead, max_words=_max_words(lead), first_max_words=_max_words(lead, front=True)
    )
    sections: list[tuple[str, list[StoryPackage]]] = []
    if lead_chunks and len(lead_chunks) > 1:
        # The remainder of the front-page lead continues at the head of its
        # own section, labelled with the validated page reference.
        remainder = lead_chunks[1:]
        section = lead.section if lead.section in SECTION_TEMPLATES else "national"
        sections.append((section, []))
        section_chunks: dict[str, list[StoryChunk]] = {
            section: [
                StoryChunk(package=lead, index=chunk.index, count=chunk.count, paragraphs=chunk.paragraphs)
                for chunk in remainder
            ]
        }
    else:
        section_chunks = {}

    for section, items in plan.sections:
        filtered = [p for p in items if p is not lead]
        if not filtered:
            continue
        entries = section_chunks.get(section, [])
        # If the lead remainder was just inserted, keep it first.
        for package in filtered:
            entries.extend(
                chunk_paragraphs(package, max_words=_max_words(package))
            )
        section_chunks[section] = entries
        sections.append((section, filtered))

    # Keep the declared section order from the planner while avoiding duplicate
    # lead sections introduced by the remainder insertion.
    seen: set[str] = set()
    ordered: list[tuple[str, list[StoryPackage]]] = []
    for section, items in sections:
        if section in seen:
            continue
        seen.add(section)
        ordered.append((section, items))
    for section in section_chunks:
        if section not in seen:
            ordered.append((section, []))

    front_briefs = [p for p in plan.front_briefs if p is not lead]
    # Short items under the lead let a thin news day still fill the tabloid
    # front page with real reports rather than white space. They are teasers
    # only; the full report still runs in its section and is page-referenced.
    rail_ids = {p.article.id for p in front_briefs}
    digest_pool = [
        p
        for p in plan.packages
        if p is not lead and p.article.id not in rail_ids and p.genre == "news"
    ]
    digest_pool.sort(
        key=lambda p: (p.section != "briefs", -float(p.story.importance or 0.0))
    )
    front_digest = digest_pool[:6]
    templates = {
        "front": "front_major_event" if lead.headline_size == "major" else "front_standard",
        "sources": "sources",
    }
    for section, _ in ordered:
        templates[section] = SECTION_TEMPLATES.get(section, "national")
    section_break: dict[str, bool] = {}
    pair_sections: set[str] = set()
    section_rows: dict[str, list[list[StoryChunk]]] = {}
    for section, _ in ordered:
        chunks = section_chunks.get(section, [])
        if (
            len(chunks) == 2
            and len({chunk.package.article.id for chunk in chunks}) == 2
        ):
            pair_sections.add(section)
        # A section whose reports each fit on one page can run two to a row.
        # Sections carrying a split report (count > 1) or the brief grid keep
        # the single-column flow.
        if (
            section != "briefs"
            and len(chunks) >= 2
            and all(chunk.count == 1 for chunk in chunks)
        ):
            section_rows[section] = [chunks[i : i + 2] for i in range(0, len(chunks), 2)]

    # Sections that must open their own page: the two feature/opinion families
    # carry a distinctive layout and the brief grid is a fixed four-column page.
    standalone = {"analysis", "opinion", "briefs"}
    previous = ""
    for index, (section, _) in enumerate(ordered):
        if index == 0:
            section_break[section] = True
            previous = section
            continue
        if section in standalone or previous in standalone:
            section_break[section] = True
            previous = section
            continue
        # Let Paged.js fill the real page. Estimating section heights here used
        # to force a page break that the rendered rows did not need, leaving
        # pages holding only a section record or the last report of a section.
        # Non-standalone sections flow; their heads carry ``break-after: avoid``
        # so a head never sits alone at the foot of a page.
        section_break[section] = False
        previous = section
    return PageChunkPlan(
        lead=lead,
        lead_chunks=lead_chunks,
        front_briefs=front_briefs,
        front_digest=front_digest,
        sections=ordered,
        section_chunks=section_chunks,
        templates=templates,
        section_break=section_break,
        pair_sections=pair_sections,
        section_rows=section_rows,
    )


def _asset_uri(value: str) -> str:
    if not value:
        return ""
    if value.startswith("file://"):
        return value
    path = Path(value)
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    if not path.exists():
        return ""
    return path.as_uri()


def _media_html(package: StoryPackage) -> Markup:
    parts: list[str] = []
    if package.media_path:
        uri = _asset_uri(package.media_path)
        if uri:
            label = (
                f'<span class="media-label">{html.escape(package.media_label)}</span>'
                if package.media_label
                else ""
            )
            caption = html.escape(package.media_caption or "Source image supplied with the channel report.")
            credit = html.escape(package.media_credit or "Source channel")
            alt = html.escape(package.media_caption or package.article.title)
            parts.append(
                f'<figure class="story__media"><img src="{html.escape(uri)}" alt="{alt}">'
                f'<figcaption>{label}{caption} <span class="credit">Credit: {credit}</span></figcaption></figure>'
            )
    elif package.map_result is not None and not package.map_result.fallback:
        result = package.map_result
        parts.append(
            '<figure class="story__media story__map">'
            + result.svg
            + f'<figcaption><strong>{html.escape(result.title)}.</strong> '
            + html.escape(result.source_note)
            + f" {html.escape(result.legend)}"
            + "</figcaption></figure>"
        )
    return Markup("".join(parts))


def _paragraphs_html(paragraphs: list[str]) -> Markup:
    return Markup(
        "".join(f"<p>{html.escape(str(paragraph))}</p>" for paragraph in paragraphs if str(paragraph).strip())
    )


def _source_refs(package: StoryPackage) -> str:
    refs = [ref for ref in package.source_refs if ref]
    if not refs:
        return "see appendix"
    if len(refs) <= 4:
        return ", ".join(refs)
    return ", ".join(refs[:4]) + f" and {len(refs) - 4} more"


def _section_label(section: str) -> str:
    return SECTION_LABELS.get(section, section.replace("_", " ").title())


def _genre_label(genre: str) -> str:
    return GENRE_LABELS.get(genre, genre.title())


def _assign_source_refs(plan: EditionPlan) -> None:
    """Give each source entry a unique appendix reference and link packages."""
    counter = 0
    by_article: dict[int, list[str]] = {}
    for entry in plan.appendix_entries:
        if entry.get("kind") == "external_link_unverified":
            ref = f"E{entry.get('article_id') or entry.get('article')}"
        else:
            counter += 1
            ref = f"A{counter}"
        entry["ref"] = ref
        article_id = entry.get("article_id")
        if article_id is not None:
            by_article.setdefault(int(article_id), []).append(ref)
    for package in plan.packages:
        package.source_refs = by_article.get(package.article.id, [])


def _edition_labels(edition: Edition, timezone: str) -> tuple[str, str, str]:
    date_label = human_local(edition.window_end, timezone, "%A, %d %B %Y")
    window_label = (
        f"Reporting window {human_local(edition.window_start, timezone, '%d %b %H:%M')} – "
        f"{human_local(edition.window_end, timezone, '%d %b %Y, %H:%M')} ({timezone})"
    )
    cutoff_label = f"Cutoff {human_local(edition.cutoff_at, timezone, '%d %b %Y, %H:%M')} {timezone}"
    return date_label, window_label, cutoff_label


def _environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.globals.update(
        section_label=_section_label,
        genre_label=_genre_label,
        media_html=_media_html,
        paragraphs_html=_paragraphs_html,
        source_refs=_source_refs,
    )
    return env


def _section_context(db: Session, plan: EditionPlan) -> dict[str, dict]:
    """Evidence-based filler for short section pages.

    A sparse page should carry useful source record, not white space. The box
    lists only events and messages that already exist in the evidence ledger.
    """
    from sqlalchemy import select

    from ..models import StoryClaim, StoryTimelineEvent

    context: dict[str, dict] = {}
    for section, items in plan.sections:
        events = []
        messages: dict[int, dict] = {}
        claims: list[StoryClaim] = []
        for package in items:
            for event in db.scalars(
                select(StoryTimelineEvent)
                .where(StoryTimelineEvent.story_id == package.story.id)
                .order_by(StoryTimelineEvent.event_at.asc())
            ):
                events.append(
                    {
                        "when": human_local(event.event_at, plan.profile.timezone, "%d %b %H:%M"),
                        "story": package.article.title,
                        "description": event.description,
                        "certainty": event.certainty,
                    }
                )
            claims.extend(
                db.scalars(select(StoryClaim).where(StoryClaim.story_id == package.story.id))
            )
            for source in package.sources:
                messages.setdefault(
                    int(source["post_id"]),
                    {
                        "message_id": source["message_id"],
                        "date": source["date_label"],
                        "url": source["source_url"],
                        "story": package.article.title,
                    },
                )
        if not events and not messages:
            continue
        context[section] = {
            "events": events[:10],
            "messages": list(messages.values())[:12],
            "claims": len(claims),
            "corrections": sum(1 for claim in claims if claim.claim_type == "correction"),
            "unverified": sum(1 for claim in claims if claim.certainty == "unverified"),
            "high_risk": sum(1 for claim in claims if claim.risk_tag),
        }
    return context


def _edition_evidence(
    db: Session, edition: Edition, plan: EditionPlan, chunk_plan: PageChunkPlan
) -> dict:
    from sqlalchemy import select

    from ..models import ArticleClaimLink, SourcePost, StoryClaim

    posts = list(
        db.scalars(
            select(SourcePost).where(
                SourcePost.channel_id == edition.channel_id,
                SourcePost.posted_at >= edition.window_start,
                SourcePost.posted_at < edition.window_end,
            )
        )
    )
    included = sum(1 for post in posts if post.inclusion_status == "included")
    excluded = sum(1 for post in posts if post.inclusion_status != "included")
    story_ids = [package.story.id for package in plan.packages]
    claims = list(db.scalars(select(StoryClaim).where(StoryClaim.story_id.in_(story_ids)))) if story_ids else []
    corrections = sum(1 for claim in claims if claim.claim_type == "correction")
    article_ids = [package.article.id for package in plan.packages]
    links = list(db.scalars(select(ArticleClaimLink).where(ArticleClaimLink.article_id.in_(article_ids)))) if article_ids else []
    supported = sum(1 for link in links if link.supported)
    review = sum(1 for package in plan.packages if package.article.status == "needs_review")
    cited_messages = sum(len({source["post_id"] for source in package.sources}) for package in plan.packages)
    return {
        "included_posts": included,
        "excluded_posts": excluded,
        "articles": len(plan.packages),
        "stories": len({package.story.id for package in plan.packages}),
        "sources": cited_messages,
        "claims": len(claims),
        "corrections": corrections,
        "claim_links": len(links),
        "supported_links": supported,
        "review_items": review,
        "pages": len(chunk_plan.section_chunks),
        "provider": (json.loads(plan.packages[0].article.audit_json or "{}").get("provider") if plan.packages else ""),
    }


def build_print_html(
    db: Session,
    edition: Edition,
    plan: EditionPlan,
    *,
    demo_edition: bool,
) -> tuple[str, PageChunkPlan]:
    _assign_source_refs(plan)
    chunk_plan = build_page_plan(plan)
    date_label, window_label, cutoff_label = _edition_labels(edition, plan.profile.timezone)
    css = CSS_PATH.read_text(encoding="utf-8")
    paged_js = (VENDOR_DIR / "paged.polyfill.min.js").read_text(encoding="utf-8")
    postprocess_js = (TEMPLATE_DIR / "postprocess.js").read_text(encoding="utf-8")
    demo_note = (
        "This is a synthetic demonstration edition built only from repository fixture data. "
        "It is not a real news report and must not be published as journalism."
        if demo_edition
        else "This edition is an editorial roundup of the owner's authorized source channel."
    )
    provenance_note = (
        f"Compiled from {plan.profile.channel_handle or 'the authorized channel'} by the publication desk. "
        "Every factual statement is traced to a dated source message in the appendix; analysis and "
        "opinion are labelled. " + demo_note
    )
    appendix_intro = (
        "Each report in this edition is tied to the dated source message(s) below. The appendix is the "
        "publication's evidence trail: it records what the channel published, when, and which item each "
        "report drew on. Where a public post link exists it is printed in full; fixture messages have no "
        "public URL and are identified by message number instead."
    )
    appendix_method = (
        f"{cutoff_label}. Article text is checked against the source corpus by an independent rule-based "
        "audit before layout. Corrections are recorded as superseding claims. No QR code is printed unless "
        "a destination is public and verified reachable; otherwise this appendix is the source of record. "
        f"{demo_note}"
    )
    appendix_footer = (
        "Publication responsibility: the owner/editor of this edition. Source posts remain the property of "
        "their author and are used under the recorded content-rights declaration for the channel. "
        "This PDF is an office-print proof, not a certified print-shop PDF/X file."
    )
    html = _environment().get_template("print.html.j2").render(
        edition=edition,
        profile=plan.profile,
        lead=chunk_plan.lead,
        lead_chunks=chunk_plan.lead_chunks,
        front_briefs=chunk_plan.front_briefs,
        front_digest=chunk_plan.front_digest,
        sections=chunk_plan.sections,
        section_chunks=chunk_plan.section_chunks,
        section_rows=chunk_plan.section_rows,
        templates=chunk_plan.templates,
        section_break=chunk_plan.section_break,
        pair_sections=chunk_plan.pair_sections,
        evidence=_edition_evidence(db, edition, plan, chunk_plan),
        section_context=_section_context(db, plan),
        front_template=chunk_plan.templates["front"],
        edition_date=date_label,
        edition_kind_label=KIND_LABELS.get(edition.kind, edition.kind.title()),
        window_label=window_label,
        appendix_entries=plan.appendix_entries,
        appendix_intro=appendix_intro,
        appendix_method=appendix_method,
        appendix_footer=appendix_footer,
        provenance_note=provenance_note,
        demo_edition=demo_edition,
        print_css=css,
        paged_js=paged_js,
        postprocess_js=postprocess_js,
    )
    return html, chunk_plan


def render_edition_pdf(
    html: str,
    *,
    html_path: Path,
    pdf_path: Path,
    preview_dir: Path,
    timeout_ms: int = 180_000,
) -> RenderResult:
    html_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    html_path = html_path.resolve()
    pdf_path = pdf_path.resolve()
    preview_dir = preview_dir.resolve()
    html_path.write_text(html, encoding="utf-8")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            channel="chrome",
            headless=True,
            args=["--allow-file-access-from-files", "--disable-gpu"],
        )
        try:
            page = browser.new_page(viewport={"width": 1056, "height": 1632}, device_scale_factor=1)
            page.goto(html_path.as_uri(), wait_until="load", timeout=timeout_ms)
            page.wait_for_function("window.__pagedReady === true", timeout=timeout_ms)
            error = page.evaluate("window.__pagedError || ''")
            if error:
                raise RuntimeError(f"Paged.js failed: {error[:800]}")
            meta = page.evaluate("window.__layoutMeta")
            pages = page.query_selector_all(".pagedjs_page")
            preview_paths: list[Path] = []
            for index, element in enumerate(pages, start=1):
                target = preview_dir / f"page-{index:02d}.png"
                element.screenshot(path=str(target))
                preview_paths.append(target)
            page.pdf(
                path=str(pdf_path),
                prefer_css_page_size=True,
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
        finally:
            browser.close()

    qa = inspect_pdf(pdf_path, meta)
    return RenderResult(
        html_path=html_path,
        pdf_path=pdf_path,
        preview_paths=preview_paths,
        pages=list(meta.get("pages", [])),
        qa=qa,
        meta=meta,
    )


def inspect_pdf(pdf_path: Path, meta: dict | None = None) -> dict:
    document = fitz.open(pdf_path)
    warnings: list[str] = []
    page_reports: list[dict] = []
    try:
        for index, page in enumerate(document, start=1):
            rect = page.rect
            width_in = round(rect.width / 72, 3)
            height_in = round(rect.height / 72, 3)
            text = page.get_text("text")
            fonts = {font[3] for font in page.get_fonts()}
            min_font = None
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if not span.get("text", "").strip():
                            continue
                        size = round(float(span.get("size", 0)), 2)
                        if size > 0 and (min_font is None or size < min_font):
                            min_font = size
            links = page.get_links()
            blank = len(text.strip()) < 40 and not page.get_images()
            if blank:
                warnings.append(f"Page {index} appears blank.")
            if min_font is not None and min_font < 7.0:
                warnings.append(f"Page {index} has text at {min_font} pt, below the 7 pt layout floor.")
            if abs(width_in - 11) > 0.01 or abs(height_in - 17) > 0.01:
                warnings.append(f"Page {index} is {width_in} x {height_in} in, expected 11 x 17 in.")
            page_reports.append(
                {
                    "page": index,
                    "width_in": width_in,
                    "height_in": height_in,
                    "text_chars": len(text),
                    "images": len(page.get_images()),
                    "links": len(links),
                    "min_font_pt": min_font,
                    "embedded_fonts": sorted(fonts),
                    "blank": blank,
                }
            )
    finally:
        page_count = document.page_count
        document.close()
    if page_count == 0:
        warnings.append("PDF contains no pages.")
    meta_pages = (meta or {}).get("pages", [])
    if meta_pages:
        for report in meta_pages:
            if report.get("blank"):
                warnings.append(f"Browser layout reported blank page {report.get('page')}.")
            if report.get("overflow"):
                details = []
                for item in report["overflow"]:
                    label = str(item.get("tag") or item.get("kind") or "element")
                    if item.get("cls"):
                        label += f".{str(item['cls']).split()[0]}"
                    if item.get("text"):
                        label += f' "{str(item["text"])[:40]}"'
                    over = item.get("over_px")
                    if isinstance(over, (int, float)):
                        label += f" (+{int(over)}px)"
                    details.append(label)
                warnings.append(
                    f"Browser layout reported overflow on page {report.get('page')}: "
                    f"{'; '.join(sorted(set(details)))}."
                )
    return {
        "page_count": page_count,
        "pages": page_reports,
        "warnings": sorted(set(warnings)),
        "expected_size_pt": {"width": 792, "height": 1224},
        "office_print_proof": not warnings,
    }


def compile_edition(
    db: Session,
    edition: Edition,
    *,
    output_dir: Path,
    demo_edition: bool,
    media_dir: Path | None = None,
) -> RenderResult:
    from ..models import PublicationProfile

    profile = db.get(PublicationProfile, 1) or db.query(PublicationProfile).first()
    if profile is None:
        raise RuntimeError("Publication profile is missing; run setup first.")
    plan = create_plan(db, edition, profile, media_dir=media_dir)
    html, _ = build_print_html(db, edition, plan, demo_edition=demo_edition)
    return render_edition_pdf(
        html,
        html_path=output_dir / "edition.html",
        pdf_path=output_dir / "edition.pdf",
        preview_dir=output_dir / "pages",
    )
