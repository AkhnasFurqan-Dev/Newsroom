"""Real print renders of a saved theme, used by the appearance screen.

The proof is a specimen tabloid page built from the same ``print.css`` and the
same compiled theme CSS as the edition renderer. It is deliberately not a news
story: every line is explanatory copy, so a colour or font choice can be judged
without pretending an unverified article is print-ready.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session

from ..config import settings
from ..logging_setup import get_logger
from ..models import PublicationProfile
from ..theming import ThemeRender, available_font_assets, compile_payload, compile_theme

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
CSS_PATH = Path(__file__).resolve().parent.parent / "web" / "static" / "css" / "print.css"
PROOF_VERSION = "4"
logger = get_logger("layout.proof")


def proof_dir(publication: PublicationProfile) -> Path:
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in publication.slug)[:80]
    target = settings.theme_proofs_dir / (safe or f"publication-{publication.id}")
    target.mkdir(parents=True, exist_ok=True)
    return target


def proof_name(theme: ThemeRender) -> str:
    digest = hashlib.sha256((theme.hash + PROOF_VERSION).encode("utf-8")).hexdigest()[:16]
    return f"theme-proof-{digest}.png"


def build_proof_html(
    publication: PublicationProfile,
    theme: ThemeRender,
) -> str:
    css = CSS_PATH.read_text(encoding="utf-8")
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
    )
    from ..theming import COLOR_FIELDS, FONT_ROLES

    theme_roles = [
        {
            "label": role["label"],
            "family": theme.payload["fonts"][role["key"]]["family"],
        }
        for role in FONT_ROLES
    ]
    palette = [
        {
            "label": field["label"],
            "value": theme.payload["colors"][field["key"]],
        }
        for field in COLOR_FIELDS
    ]
    return environment.get_template("theme_proof.html.j2").render(
        publication=publication,
        theme=theme,
        theme_roles=theme_roles,
        palette=palette,
        print_css=css,
    )


def _launch(playwright):
    options = {"headless": True, "args": ["--allow-file-access-from-files", "--disable-gpu"]}
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
    for candidate in chrome_candidates:
        if candidate.exists():
            return playwright.chromium.launch(channel="chrome", **options)
    for candidate in edge_candidates:
        if candidate.exists():
            return playwright.chromium.launch(channel="msedge", **options)
    return playwright.chromium.launch(**options)


def render_theme_proof(
    db: Session,
    publication: PublicationProfile,
    *,
    payload: dict | None = None,
) -> tuple[Path, ThemeRender]:
    """Render (or reuse) a specimen page and return its PNG path."""
    target = proof_dir(publication)
    if payload is None:
        theme = compile_theme(db, publication, None, font_dir=target)
    else:
        theme = compile_payload(
            payload,
            source="draft",
            assets=available_font_assets(db),
            font_dir=target,
        )
    image_path = target / proof_name(theme)
    if image_path.exists() and image_path.stat().st_size > 5_000:
        return image_path, theme
    html_path = target / f"{image_path.stem}.html"
    html_path.write_text(build_proof_html(publication, theme), encoding="utf-8")
    timeout = max(10, int(settings.layout_browser_timeout_seconds)) * 1000
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        page = None
        try:
            page = browser.new_page(viewport={"width": 1056, "height": 1632}, device_scale_factor=1)
            page.set_default_timeout(timeout)
            page.goto(html_path.resolve().as_uri(), wait_until="load", timeout=timeout)
            page.wait_for_function("document.fonts.status === 'loaded'", timeout=timeout)
            sheet = page.query_selector(".proof-sheet")
            if sheet is None:
                raise RuntimeError("The theme proof page did not render its print sheet.")
            sheet.screenshot(path=str(image_path))
        finally:
            if page is not None:
                page.close()
            browser.close()
    return image_path, theme
