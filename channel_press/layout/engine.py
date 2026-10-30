from __future__ import annotations

import html
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import fitz
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup
from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session

from ..config import BASE_DIR, settings
from ..constants import SECTION_LABELS
from ..logging_setup import get_logger
from ..models import Edition
from ..timeutil import human_local
from .planner import EditionPlan, StoryPackage, create_plan

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
VENDOR_DIR = BASE_DIR / "channel_press" / "web" / "static" / "vendor"
CSS_PATH = BASE_DIR / "channel_press" / "web" / "static" / "css" / "print.css"
logger = get_logger("layout")

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

KIND_PUBLIC_NAMES = {
    "daily": "Daily Edition",
    "weekly_roundup": "Weekly Edition",
    "monthly_roundup": "Monthly Edition",
    "special": "Special Edition",
    # "Manual" describes how a job was started, not what the reader is holding.
    "manual": "Edition",
}


def edition_display_name(edition: Edition) -> str:
    """Reader-facing edition name.

    An editor-supplied name always wins. Legacy editions without one receive a
    neutral publication label derived from the internal kind; the historic
    database rows are never rewritten.
    """
    name = (edition.display_name or "").strip()
    if name:
        return name
    return KIND_PUBLIC_NAMES.get(edition.kind, "Edition")


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
    plan: EditionPlan | None = None
    theme: object | None = None


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


# A two-story row gives each report half the measure, so the pair must stay
# within one tabloid page. The estimate deliberately errs towards the full-width
# flow: a row that cannot fit leaves a blank page behind it in Paged.js.
PAIRABLE_SECTIONS = {"national", "regional", "international", "defence"}
ROW_WORD_BUDGET = 980
ROW_SINGLE_BUDGET = 620
# A section is only given a fresh tabloid page when its real reports are likely
# to fill enough of it. Thin sections flow after the previous one instead of
# leaving a mostly empty page (and no process ever invents filler copy).
SECTION_BREAK_MIN_LOAD = 760
# Comment and editorial copy is a newspaper's own voice and is not broken across
# a page boundary after a line or two of body text. Giving the opinion page its
# own opening also keeps a short editorial from being stranded at the foot of a
# section page.
OPINION_BREAK_SECTIONS = {"opinion"}


def _package_load(package: StoryPackage) -> int:
    """Rough vertical load of a report in half-page row measure."""
    load = _words(package.body_paragraphs) + len((package.article.title or "").split()) * 2
    if package.article.standfirst:
        load += len(package.article.standfirst.split()) // 2
    if package.media_path:
        load += 90
    if package.supporting_media_path:
        load += 45
    if package.map_result is not None:
        load += 150
    if package.chart_svg:
        load += 110
    if package.timeline_html:
        load += 35 * package.timeline_html.count("<li")
    if package.correction_note:
        load += 25
    return load


def _row_fits(chunks: list[StoryChunk]) -> bool:
    loads = [_package_load(chunk.package) for chunk in chunks]
    return (
        bool(loads)
        and max(loads) <= ROW_SINGLE_BUDGET
        and sum(loads) <= ROW_WORD_BUDGET
    )


def _section_needs_break(section: str, chunks: list[StoryChunk]) -> bool:
    if not chunks:
        return False
    if section in OPINION_BREAK_SECTIONS:
        return True
    load = sum(_package_load(chunk.package) for chunk in chunks)
    if section == "briefs":
        load += 80
    return load >= SECTION_BREAK_MIN_LOAD


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
            and _row_fits(chunks)
        ):
            pair_sections.add(section)
        # A section whose reports each fit on one page can run two to a row.
        # Sections carrying a split report (count > 1) or the brief grid keep
        # the single-column flow.
        if (
            section in PAIRABLE_SECTIONS
            and len(chunks) >= 2
            and all(chunk.count == 1 for chunk in chunks)
        ):
            rows: list[list[StoryChunk]] = []
            pending: list[StoryChunk] = []
            for chunk in chunks:
                if pending and not _row_fits([*pending, chunk]):
                    rows.append(pending)
                    pending = [chunk]
                else:
                    pending.append(chunk)
                if len(pending) == 2:
                    rows.append(pending)
                    pending = []
            if pending:
                # A lone final report stays full width when the previous row is
                # already substantial; pairing it with a re-shuffled row would
                # make one row overflow the page.
                rows.append(pending)
            if len(rows) != len(chunks):
                section_rows[section] = rows

    # Give a section its own page only when its real content can justify one.
    # Thin sections flow behind the previous section; Paged.js then places the
    # section head with its first report (``break-after: avoid``) instead of
    # leaving an almost empty page. The first interior section always opens a
    # page after the front.
    for index, (section, _) in enumerate(ordered):
        section_break[section] = index == 0 or _section_needs_break(
            section, section_chunks.get(section, [])
        )
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

    def _figure(path: str, caption: str, credit: str, label: str, *, extra: str = "") -> str:
        uri = _asset_uri(path)
        if not uri:
            return ""
        label_html = f'<span class="media-label">{html.escape(label)}</span>' if label else ""
        caption_html = html.escape(caption or "Source image supplied with the channel report.")
        credit_html = html.escape(credit or "Source channel")
        alt = html.escape(caption or package.article.title)
        return (
            f'<figure class="story__media{extra}"><img src="{html.escape(uri)}" alt="{alt}">'
            f"<figcaption>{label_html}{caption_html} "
            f'<span class="credit">Credit: {credit_html}</span></figcaption></figure>'
        )

    if package.media_path:
        parts.append(
            _figure(
                package.media_path,
                package.media_caption,
                package.media_credit,
                package.media_label,
            )
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
    if package.supporting_media_path:
        parts.append(
            _figure(
                package.supporting_media_path,
                package.supporting_media_caption,
                package.supporting_media_credit,
                package.supporting_media_label,
                extra=" story__media--supporting",
            )
        )
    return Markup("".join(part for part in parts if part))


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


def _attach_citations(plan: EditionPlan, *, demo_edition: bool) -> None:
    """Attach a compact QR citation or an explicit no-link label to each entry.

    A code is printed only for a public direct Telegram source URL. Fixture
    messages have no public destination and never receive a fake code. External
    web references stay digital-only until an editor has verified them, so the
    printed appendix does not promote an unvetted link.
    """
    from ..publishing.qr import citation_code

    for entry in plan.appendix_entries:
        kind = entry.get("kind")
        url = (entry.get("url") or "").strip()
        if demo_edition or kind == "fixture" or not url:
            entry["qr_data_uri"] = ""
            entry["qr_target"] = ""
            entry["qr_printed"] = False
            entry["qr_reason"] = "fixture_has_no_public_url" if demo_edition else "no_public_link"
            entry["citation_label"] = "No public link"
            continue
        if kind == "external_link_unverified":
            entry["qr_data_uri"] = ""
            entry["qr_target"] = ""
            entry["qr_printed"] = False
            entry["qr_reason"] = "external_link_not_printed"
            entry["citation_label"] = "External link not printed"
            continue
        code = citation_code(url, label=f"Message {entry.get('message_id') or ''}".strip())
        entry["qr_data_uri"] = code.data_uri
        entry["qr_target"] = code.target
        entry["qr_printed"] = code.available
        entry["qr_reason"] = code.reason
        if code.available:
            entry["citation_label"] = f"Scan · msg {entry.get('message_id') or ''}".strip()
        else:
            entry["citation_label"] = "Link withheld (not public)"



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


_PUBLIC_TELEGRAM = re.compile(
    r"^https?://(?:t\.me|telegram\.me)/([A-Za-z][A-Za-z0-9_]{3,})/(\d+)(?:[/?#].*)?$"
)


def _public_telegram_url(value: str) -> str:
    """Return a direct, publicly addressable Telegram post URL, else ''.

    Private invite links (``t.me/+...``), ``t.me/c/...`` internal links and
    placeholder addresses are never printed as a QR target.
    """
    value = (value or "").strip()
    match = _PUBLIC_TELEGRAM.match(value)
    if not match:
        return ""
    return f"https://t.me/{match.group(1)}/{match.group(2)}"


def _public_base(profile) -> str:
    """Configured, explicitly verified public origin, or ''."""
    base = (getattr(profile, "public_base_url", "") or "").strip().rstrip("/")
    if not base or not getattr(profile, "public_base_url_verified", False):
        return ""
    lowered = base.lower()
    if not lowered.startswith("https://"):
        return ""
    if any(token in lowered for token in ("localhost", "127.0.0.1", "[::1]", "file:")):
        return ""
    return base


def _attach_story_qr(plan: EditionPlan, edition: Edition, *, demo_edition: bool) -> None:
    """Resolve one compact QR per story, or an explicit editor-only status.

    Resolution order (see P09):
      1. a verified public evidence page under the configured publication origin;
      2. a verified public Telegram post URL for the primary source message;
      3. no code at all, with an actionable reason for the editor.
    """
    from ..publishing.qr import citation_code

    base = _public_base(plan.profile)
    external_articles = {
        int(entry["article_id"])
        for entry in plan.appendix_entries
        if entry.get("article_id") is not None
        and entry.get("kind") == "external_link_unverified"
    }
    for package in plan.packages:
        package.public_id = f"e{edition.id}-a{package.article.id}"
        public_sources = [
            _public_telegram_url(source.get("source_url") or "")
            for source in package.sources
        ]
        public_sources = [url for url in public_sources if url]
        package.primary_source_url = public_sources[0] if public_sources else ""
        target = ""
        reason = ""
        label = "Scan for sources"
        if demo_edition:
            reason = "demo_edition_no_public_target"
        elif base and (public_sources or package.article.id in external_articles):
            target = f"{base}/sources/{package.public_id}/"
            label = "Scan for sources"
        elif package.primary_source_url:
            target = package.primary_source_url
            label = "Scan for original post"
        else:
            reason = "publish_destination_required"
        if not target:
            package.qr_status = "unavailable"
            package.qr_reason = reason
            package.qr_label = ""
            continue
        code = citation_code(target, label=label)
        package.qr_target = code.target
        package.qr_reason = code.reason
        if code.available:
            package.qr_data_uri = code.data_uri
            package.qr_status = "ok"
            package.qr_label = label
        else:
            package.qr_status = "unavailable"
            package.qr_label = ""


def build_print_html(
    db: Session,
    edition: Edition,
    plan: EditionPlan,
    *,
    demo_edition: bool,
    theme=None,
) -> tuple[str, PageChunkPlan]:
    _assign_source_refs(plan)
    _attach_citations(plan, demo_edition=demo_edition)
    _attach_story_qr(plan, edition, demo_edition=demo_edition)
    chunk_plan = build_page_plan(plan)
    date_label, _window_label, _cutoff_label = _edition_labels(edition, plan.profile.timezone)
    css = CSS_PATH.read_text(encoding="utf-8")
    if theme is None:
        from ..theming import compile_theme

        theme = compile_theme(db, plan.profile, edition)
    css = css + "\n/* publication theme */\n" + theme.css + "\n"
    paged_js = (VENDOR_DIR / "paged.polyfill.min.js").read_text(encoding="utf-8")
    postprocess_js = (TEMPLATE_DIR / "postprocess.js").read_text(encoding="utf-8")
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
        front_template=chunk_plan.templates["front"],
        edition_date=date_label,
        edition_name=edition_display_name(edition),
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
    progress: Callable[[str, str], None] | None = None,
) -> RenderResult:
    def notify(stage: str, detail: str) -> None:
        if progress:
            progress(stage, detail)

    html_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    html_path = html_path.resolve()
    pdf_path = pdf_path.resolve()
    preview_dir = preview_dir.resolve()
    notify("layout_compile_html", f"Writing print HTML to {html_path.name}")
    html_path.write_text(html, encoding="utf-8")

    with sync_playwright() as playwright:
        browser_timeout = max(1, int(settings.layout_browser_timeout_seconds * 1000))
        launch_attempts: list[dict[str, str]] = []
        chrome_candidates = [
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files"))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Google"
            / "Chrome"
            / "Application"
            / "chrome.exe",
        ]
        edge_candidates = [
            Path(os.environ.get("PROGRAMFILES", "C:\\Program Files"))
            / "Microsoft"
            / "Edge"
            / "Application"
            / "msedge.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"))
            / "Microsoft"
            / "Edge"
            / "Application"
            / "msedge.exe",
        ]
        browser_options = {"headless": True, "args": ["--allow-file-access-from-files", "--disable-gpu"]}
        browser = None
        notify("layout_launch_browser", "Starting the print browser")
        for candidate in chrome_candidates:
            if candidate.exists():
                launch_attempts.append({"browser": "chrome", "path": str(candidate)})
                browser = playwright.chromium.launch(
                    channel="chrome", timeout=browser_timeout, **browser_options
                )
                break
        if browser is None:
            for candidate in edge_candidates:
                if candidate.exists():
                    launch_attempts.append({"browser": "edge", "path": str(candidate)})
                    browser = playwright.chromium.launch(
                        channel="msedge", timeout=browser_timeout, **browser_options
                    )
                    break
        if browser is None:
            launch_attempts.append({"browser": "playwright-chromium", "path": ""})
            try:
                browser = playwright.chromium.launch(timeout=browser_timeout, **browser_options)
            except Exception as exc:
                attempted = ", ".join(item["browser"] for item in launch_attempts)
                raise RuntimeError(
                    "No supported print browser could be launched. "
                    f"Tried {attempted}; install Google Chrome or run "
                    "'playwright install chromium'."
                ) from exc
        page = None
        try:
            notify("layout_load_page", "Opening the local print document")
            page = browser.new_page(viewport={"width": 1056, "height": 1632}, device_scale_factor=1)
            page.on("console", lambda message: logger.debug("layout_browser_console %s", message.text))
            page.on("pageerror", lambda error: logger.error("layout_browser_error %s", error))
            page.set_default_timeout(timeout_ms)
            page.goto(html_path.as_uri(), wait_until="load", timeout=timeout_ms)
            notify("layout_wait_for_pagination", "Waiting for Paged.js pagination to complete")
            page.wait_for_function("window.__pagedReady === true", timeout=timeout_ms)
            error = page.evaluate("window.__pagedError || ''")
            if error:
                raise RuntimeError(f"Paged.js failed: {error[:800]}")
            meta = page.evaluate("window.__layoutMeta")
            pages = page.query_selector_all(".pagedjs_page")
            notify("layout_generate_pdf", f"Exporting {len(pages)} paginated pages to PDF")
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
            notify("layout_validate_pdf", "Validating generated PDF geometry and content")
        except Exception:
            if page is not None:
                screenshot = preview_dir / "layout-error.png"
                try:
                    page.screenshot(path=str(screenshot), full_page=True, timeout=10_000)
                except Exception:
                    pass
            raise
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
    layout_pages: list[dict] = []
    if meta_pages:
        for report in meta_pages:
            if report.get("blank"):
                warnings.append(f"Browser layout reported blank page {report.get('page')}.")
            fill = report.get("fill_ratio")
            if isinstance(fill, (int, float)):
                layout_pages.append(
                    {
                        "page": report.get("page"),
                        "template": report.get("template", ""),
                        "section": report.get("section", ""),
                        "fill_ratio": round(float(fill), 3),
                        "used_height_px": report.get("used_height_px"),
                        "content_height_px": report.get("content_height_px"),
                        "trailing_whitespace_px": report.get("trailing_whitespace_px"),
                        "underfull": bool(report.get("underfull")),
                    }
                )
            if report.get("orphan_section_head"):
                warnings.append(
                    f"Page {report.get('page')} carries a section heading with no reports."
                )
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
    underfull = [page for page in layout_pages if page["underfull"]]
    fills = [page["fill_ratio"] for page in layout_pages]
    layout = {
        "pages": layout_pages,
        "underfull_pages": [page["page"] for page in underfull],
        "underfull_count": len(underfull),
        "average_fill_ratio": round(sum(fills) / len(fills), 3) if fills else 0.0,
        "lowest_fill_ratio": min(fills) if fills else 0.0,
        "trailing_whitespace_px": sum(
            int(page.get("trailing_whitespace_px") or 0) for page in layout_pages
        ),
        "measured": bool(layout_pages),
        "note": (
            "Fill is the share of the printed content box occupied by real text, "
            "figures and tables. Underfull pages are reported for the editor; only "
            "blank pages, overflowing geometry and orphaned section headings fail proofing."
        ),
    }
    return {
        "page_count": page_count,
        "pages": page_reports,
        "warnings": sorted(set(warnings)),
        "layout": layout,
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
    progress: Callable[[str, str], None] | None = None,
) -> RenderResult:
    from ..publications import publication_for_edition
    from ..theming import compile_theme

    profile = publication_for_edition(db, edition)
    if profile is None:
        raise RuntimeError("Publication profile is missing; run setup first.")
    if progress:
        progress("layout_prepare", "Building page plan from the real edition articles")
    theme = compile_theme(db, profile, edition, font_dir=output_dir)
    plan = create_plan(
        db,
        edition,
        profile,
        media_dir=media_dir,
        download_media=False,
        palette=theme.palette,
        chart_font=theme.font_stacks["numeric"],
        map_font=theme.font_stacks["caption"],
    )
    html, _ = build_print_html(
        db,
        edition,
        plan,
        demo_edition=demo_edition,
        theme=theme,
    )
    if db.info.get("durable_worker"):
        db.commit()
    result = render_edition_pdf(
        html,
        html_path=output_dir / "edition.html",
        pdf_path=output_dir / "edition.pdf",
        preview_dir=output_dir / "pages",
        timeout_ms=max(1, int(settings.layout_pagedjs_timeout_seconds * 1000)),
        progress=progress,
    )
    if progress:
        progress("layout_complete", f"PDF validated with {result.qa.get('page_count', 0)} pages")
    result.plan = plan
    result.theme = theme
    return result
