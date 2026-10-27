"""Per-newspaper print identity: colours, fonts and versioned themes.

The saved theme is authoritative for print. It is compiled to CSS used by the
PDF renderer, the theme proof and the e-paper, and a copy is pinned into every
published edition so archived issues keep the identity they were printed with.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .constants import SECTION_ORDER
from .fonts import (
    asset_face,
    discover_installed_fonts,
    glyph_coverage,
    resolve_face,
    role_samples,
)
from .models import Edition, FontAsset, PublicationProfile, ThemeRevision
from .timeutil import utcnow

THEME_VERSION = "print-theme-2026-10-27-v1"

HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_SAFE_CSS_FAMILY = re.compile(r"[^A-Za-z0-9 ._\-+]")

COLOR_FIELDS: tuple[dict[str, str], ...] = (
    {
        "key": "primary",
        "label": "Primary / masthead",
        "default": "#171614",
        "help": "Masthead, core identity and major display headings.",
    },
    {
        "key": "accent",
        "label": "Accent",
        "default": "#7b1f24",
        "help": "Kickers, badges, section rules and callouts. Kept out of body text.",
    },
    {
        "key": "secondary",
        "label": "Secondary",
        "default": "#4a4740",
        "help": "Supplementary section detail such as running heads and folios.",
    },
    {
        "key": "ink",
        "label": "Body ink",
        "default": "#171614",
        "help": "Article text. Must stay highly legible on the paper tone.",
    },
    {
        "key": "muted",
        "label": "Muted text",
        "default": "#4d4a44",
        "help": "Bylines, captions, metadata and dates.",
    },
    {
        "key": "paper",
        "label": "Page / paper",
        "default": "#fffdf8",
        "help": "Printed page background. Print work should stay light.",
    },
    {
        "key": "surface",
        "label": "Panel / surface",
        "default": "#f3eee3",
        "help": "Evidence boxes, shaded briefs and pull-quote panels.",
    },
    {
        "key": "rule",
        "label": "Rules / borders",
        "default": "#2b2926",
        "help": "Masthead rules, section borders and dividers.",
    },
    {
        "key": "link",
        "label": "Link / QR labels",
        "default": "#3f3d38",
        "help": "Printed citation labels and digital link text.",
    },
)

FONT_ROLES: tuple[dict[str, Any], ...] = (
    {
        "key": "display",
        "label": "Masthead / display",
        "default": {"family": "Georgia", "weight": 700, "style": "normal", "fallback": "serif"},
        "sample": "THE CHANNEL DISPATCH",
    },
    {
        "key": "headline",
        "label": "Article headline",
        "default": {"family": "Georgia", "weight": 700, "style": "normal", "fallback": "serif"},
        "sample": "Market reopens after blast",
    },
    {
        "key": "section",
        "label": "Section heading",
        "default": {"family": "Georgia", "weight": 700, "style": "normal", "fallback": "serif"},
        "sample": "International",
    },
    {
        "key": "standfirst",
        "label": "Standfirst / deck",
        "default": {"family": "Segoe UI", "weight": 400, "style": "normal", "fallback": "sans-serif"},
        "sample": "Officials said the wounded were taken to hospital.",
    },
    {
        "key": "body",
        "label": "Article body",
        "default": {"family": "Georgia", "weight": 400, "style": "normal", "fallback": "serif"},
        "sample": "The district administration said traffic had resumed.",
    },
    {
        "key": "caption",
        "label": "Caption / byline",
        "default": {"family": "Segoe UI", "weight": 400, "style": "normal", "fallback": "sans-serif"},
        "sample": "By the Editorial Desk · updated 05:00 PKT",
    },
    {
        "key": "numeric",
        "label": "Numbers / infographics",
        "default": {"family": "Segoe UI", "weight": 700, "style": "normal", "fallback": "sans-serif"},
        "sample": "12 dead · 34% · 2026",
    },
)

COLOR_KEYS = tuple(item["key"] for item in COLOR_FIELDS)
FONT_ROLE_KEYS = tuple(item["key"] for item in FONT_ROLES)
FALLBACKS = {"serif", "sans-serif", "monospace"}

METRIC_LIMITS = {
    "body_size_pt": (8.5, 11.5),
    "body_leading": (1.2, 1.62),
    "headline_scale": (0.85, 1.18),
    "headline_tracking": (-0.04, 0.02),
    "display_scale": (0.78, 1.12),
}
METRIC_DEFAULTS = {
    "body_size_pt": 9.45,
    "body_leading": 1.43,
    "headline_scale": 1.0,
    "headline_tracking": -0.02,
    "display_scale": 1.0,
}


class ThemeError(ValueError):
    """A saved palette or font selection cannot be accepted."""


PRESETS: tuple[dict[str, Any], ...] = (
    {
        "key": "traditional",
        "name": "Traditional broadsheet",
        "description": "Near-black ink, crimson accent and a warm paper tone.",
        "colors": {
            "primary": "#171614",
            "accent": "#7b1f24",
            "secondary": "#4a4740",
            "ink": "#171614",
            "muted": "#4d4a44",
            "paper": "#fffdf8",
            "surface": "#f3eee3",
            "rule": "#2b2926",
            "link": "#3f3d38",
        },
    },
    {
        "key": "contemporary",
        "name": "Restrained contemporary",
        "description": "Deep navy identity, cool grey rules and clean white paper.",
        "colors": {
            "primary": "#1d2a38",
            "accent": "#1f4e79",
            "secondary": "#3d4f61",
            "ink": "#1b1d1f",
            "muted": "#4b535c",
            "paper": "#ffffff",
            "surface": "#eef2f6",
            "rule": "#33414f",
            "link": "#2a4d6f",
        },
    },
    {
        "key": "monochrome",
        "name": "Monochrome",
        "description": "Black, warm greys and no chromatic accent; prints cleanly in one ink.",
        "colors": {
            "primary": "#111111",
            "accent": "#333333",
            "secondary": "#4a4a4a",
            "ink": "#111111",
            "muted": "#4f4f4f",
            "paper": "#fffefa",
            "surface": "#f0efeb",
            "rule": "#222222",
            "link": "#333333",
        },
    },
    {
        "key": "forest",
        "name": "Quiet forest",
        "description": "Dark green identity with restrained ochre detail.",
        "colors": {
            "primary": "#1e2f27",
            "accent": "#3f6b4f",
            "secondary": "#7a5c2e",
            "ink": "#1a1d1a",
            "muted": "#4c544e",
            "paper": "#fffdf7",
            "surface": "#eef1e9",
            "rule": "#2c3d33",
            "link": "#35553f",
        },
    },
)


def default_payload() -> dict[str, Any]:
    return {
        "version": THEME_VERSION,
        "colors": {item["key"]: item["default"] for item in COLOR_FIELDS},
        "fonts": {item["key"]: dict(item["default"]) for item in FONT_ROLES},
        "metrics": dict(METRIC_DEFAULTS),
        "sections": {},
    }


def preset(key: str) -> dict[str, Any] | None:
    return next((item for item in PRESETS if item["key"] == key), None)


def normalise_hex(value: str, *, field: str = "colour") -> str:
    raw = (value or "").strip()
    match = HEX_COLOR.fullmatch(raw)
    if not match:
        raise ThemeError(f"{field} must be a six-digit hex colour such as #7b1f24.")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(char * 2 for char in digits)
    return "#" + digits.lower()


def _color_tuple(value: str) -> tuple[int, int, int]:
    value = normalise_hex(value)
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))  # type: ignore[return-value]


def relative_luminance(value: str) -> float:
    def channel(raw: int) -> float:
        scale = raw / 255
        return scale / 12.92 if scale <= 0.03928 else ((scale + 0.055) / 1.055) ** 2.4

    red, green, blue = _color_tuple(value)
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    light = relative_luminance(foreground)
    dark = relative_luminance(background)
    if light < dark:
        light, dark = dark, light
    return round((light + 0.05) / (dark + 0.05), 2)


def mix(first: str, second: str, weight: float = 0.5) -> str:
    """Blend two colours; used to derive hairlines from the rule colour."""
    weight = max(0.0, min(1.0, weight))
    a = _color_tuple(first)
    b = _color_tuple(second)
    return "#" + "".join(
        f"{round(a[index] * weight + b[index] * (1 - weight)):02x}" for index in range(3)
    )


def normalise_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Validate colours, font selections and bounded typographic metrics."""
    base = default_payload()
    colors_in = data.get("colors") or {}
    colors: dict[str, str] = {}
    for item in COLOR_FIELDS:
        key = item["key"]
        raw = colors_in.get(key, base["colors"][key])
        try:
            colors[key] = normalise_hex(str(raw), field=item["label"])
        except ThemeError as exc:
            raise ThemeError(f"{item['label']}: {exc}") from exc
    fonts_in = data.get("fonts") or {}
    fonts: dict[str, dict[str, Any]] = {}
    for item in FONT_ROLES:
        key = item["key"]
        raw = fonts_in.get(key) or base["fonts"][key]
        if not isinstance(raw, dict):
            raise ThemeError(f"{item['label']} must be a font selection.")
        family = str(raw.get("family") or base["fonts"][key]["family"]).strip()
        if not family or len(family) > 160:
            raise ThemeError(f"Choose a family for {item['label']}.")
        try:
            weight = int(raw.get("weight") or 400)
        except (TypeError, ValueError) as exc:
            raise ThemeError(f"{item['label']}: font weight must be a number.") from exc
        if weight not in {100, 200, 300, 400, 500, 600, 700, 800, 900}:
            raise ThemeError(f"{item['label']}: unsupported font weight {weight}.")
        style = str(raw.get("style") or "normal").lower()
        if style not in {"normal", "italic"}:
            raise ThemeError(f"{item['label']}: font style must be normal or italic.")
        fallback = str(raw.get("fallback") or base["fonts"][key]["fallback"]).lower()
        if fallback not in FALLBACKS:
            raise ThemeError(f"{item['label']}: unknown font fallback.")
        asset_id = raw.get("asset_id")
        if asset_id in ("", None):
            asset_id = None
        else:
            try:
                asset_id = int(asset_id)
            except (TypeError, ValueError) as exc:
                raise ThemeError(f"{item['label']}: invalid imported font reference.") from exc
        fonts[key] = {
            "family": family,
            "weight": weight,
            "style": style,
            "fallback": fallback,
            "asset_id": asset_id,
        }
    metrics_in = data.get("metrics") or {}
    metrics: dict[str, float] = {}
    for key, (low, high) in METRIC_LIMITS.items():
        try:
            value = float(metrics_in.get(key, base["metrics"][key]))
        except (TypeError, ValueError) as exc:
            raise ThemeError(f"{key.replace('_', ' ')} must be a number.") from exc
        if not low <= value <= high:
            raise ThemeError(f"{key.replace('_', ' ')} must be between {low} and {high}.")
        metrics[key] = round(value, 3)
    sections_in = data.get("sections") or {}
    sections: dict[str, str] = {}
    if isinstance(sections_in, dict):
        for key, raw in sections_in.items():
            if key not in SECTION_ORDER or not raw:
                continue
            sections[key] = normalise_hex(str(raw), field=f"{key} section accent")
    return {"version": THEME_VERSION, "colors": colors, "fonts": fonts, "metrics": metrics, "sections": sections}


def payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def contrast_warnings(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Accessible-print warnings. Risky choices are reported, not rewritten."""
    colors = payload["colors"]
    paper = colors["paper"]
    checks = [
        ("ink", 7.0, "Body ink"),
        ("primary", 4.5, "Primary/masthead"),
        ("muted", 4.5, "Muted text"),
        ("accent", 3.0, "Accent"),
        ("secondary", 3.0, "Secondary"),
        ("link", 4.5, "Link/QR labels"),
        ("rule", 2.4, "Rules"),
    ]
    warnings: list[dict[str, str]] = []
    for key, minimum, label in checks:
        ratio = contrast_ratio(colors[key], paper)
        if ratio < minimum:
            warnings.append(
                {
                    "level": "critical" if key in {"ink", "muted"} and ratio < 3.0 else "warning",
                    "code": f"contrast_{key}",
                    "message": f"{label} contrast on the paper tone is {ratio:.2f}:1 (recommend {minimum:.1f}:1).",
                }
            )
    if contrast_ratio(colors["ink"], colors["surface"]) < 4.5:
        warnings.append(
            {
                "level": "warning",
                "code": "contrast_surface",
                "message": "Body ink contrast inside shaded panels is below 4.5:1.",
            }
        )
    if relative_luminance(paper) < 0.72:
        warnings.append(
            {
                "level": "warning",
                "code": "paper_too_dark",
                "message": "The paper tone is dark for offset/laser printing and will use heavy ink coverage.",
            }
        )
    if contrast_ratio("#111111", "#ffffff") < 7:  # pragma: no cover - invariant guard
        warnings.append(
            {
                "level": "critical",
                "code": "qr_contrast",
                "message": "QR modules must stay dark on light; the renderer forces black on white.",
            }
        )
    return warnings


def theme_hash_for_publication(db: Session, publication: PublicationProfile) -> str:
    theme = current_theme(db, publication)
    return payload_hash(theme_payload(theme))


def theme_palette(payload: dict[str, Any]) -> dict[str, str]:
    """Colours plus the derived tints used by rules and secondary text."""
    colors = payload["colors"]
    return {
        **colors,
        "ink_soft": mix(colors["ink"], colors["paper"], 0.82),
        "hair": mix(colors["rule"], colors["paper"], 0.45),
        "hairline": mix(colors["rule"], colors["paper"], 0.22),
    }


def theme_font_stacks(payload: dict[str, Any], assets: list[FontAsset]) -> dict[str, str]:
    resolved, _warnings = _select_fonts(payload, assets)
    return {role: resolved[role]["stack"] for role in FONT_ROLE_KEYS}


@dataclass
class ThemeRender:
    """Everything one render needs from a saved revision."""

    theme: ThemeRevision | None
    payload: dict[str, Any]
    source: str
    css: str
    warnings: list[dict[str, str]]
    hash: str
    palette: dict[str, str]
    font_stacks: dict[str, str]

    @property
    def revision_id(self) -> int | None:
        return self.theme.id if self.theme is not None else None


def compile_theme(
    db: Session,
    publication: PublicationProfile,
    edition: Edition | None = None,
    *,
    font_dir: Path | None = None,
    font_url_prefix: str = "",
    assets: list[FontAsset] | None = None,
) -> ThemeRender:
    """Resolve, compile and describe the theme for one rendering target."""
    theme, payload, source = resolve_edition_theme(db, publication, edition)
    return compile_payload(
        payload,
        theme=theme,
        source=source,
        font_dir=font_dir,
        font_url_prefix=font_url_prefix,
        assets=available_font_assets(db) if assets is None else assets,
    )


def compile_payload(
    payload: dict[str, Any],
    *,
    theme: ThemeRevision | None = None,
    source: str = "draft",
    font_dir: Path | None = None,
    font_url_prefix: str = "",
    assets: list[FontAsset] | None = None,
) -> ThemeRender:
    """Compile a validated payload without saving it (used by the live proof)."""
    font_assets = [] if assets is None else assets
    css, warnings = theme_css(
        payload,
        assets=font_assets,
        font_dir=font_dir,
        font_url_prefix=font_url_prefix,
    )
    return ThemeRender(
        theme=theme,
        payload=payload,
        source=source,
        css=css,
        warnings=warnings,
        hash=payload_hash(payload),
        palette=theme_palette(payload),
        font_stacks=theme_font_stacks(payload, font_assets),
    )


def pin_edition_theme(
    db: Session,
    edition: Edition,
    publication: PublicationProfile,
    render: ThemeRender,
) -> None:
    """Record the exact theme used for a release; never rewrite a published one."""
    if edition.status == "published":
        return
    edition.theme_revision_id = render.revision_id
    edition.theme_snapshot_json = json.dumps(render.payload, ensure_ascii=False, sort_keys=True)
    edition.preview_theme_hash = render.hash
    edition.preview_state = "current"


def initial_theme_name(publication: PublicationProfile) -> str:
    return f"{publication.name} standard"


def current_theme(db: Session, publication: PublicationProfile) -> ThemeRevision:
    """Return the current revision, creating a legacy-compatible default."""
    row = db.scalar(
        select(ThemeRevision)
        .where(
            ThemeRevision.publication_id == publication.id,
            ThemeRevision.is_current.is_(True),
        )
        .order_by(ThemeRevision.revision.desc())
        .limit(1)
    )
    if row is not None:
        return row
    payload = default_payload()
    # A legacy profile only stored one accent colour; carry it forward instead
    # of discarding the owner's existing branding.
    try:
        payload["colors"]["accent"] = normalise_hex(publication.accent or "#7b1f24")
    except ThemeError:
        payload["colors"]["accent"] = "#7b1f24"
    row = ThemeRevision(
        publication_id=publication.id,
        revision=1,
        name=initial_theme_name(publication),
        is_current=True,
        colors_json=json.dumps(payload["colors"], ensure_ascii=False),
        fonts_json=json.dumps(
            {"fonts": payload["fonts"], "metrics": payload["metrics"], "sections": payload["sections"]},
            ensure_ascii=False,
        ),
        notes="Created from the publication's existing accent colour.",
    )
    db.add(row)
    db.flush()
    return row


def theme_payload(theme: ThemeRevision) -> dict[str, Any]:
    try:
        colors = json.loads(theme.colors_json or "{}")
    except ValueError:
        colors = {}
    try:
        fonts_blob = json.loads(theme.fonts_json or "{}")
    except ValueError:
        fonts_blob = {}
    data = {
        "colors": colors,
        "fonts": fonts_blob.get("fonts") if isinstance(fonts_blob, dict) else {},
        "metrics": fonts_blob.get("metrics") if isinstance(fonts_blob, dict) else {},
        "sections": fonts_blob.get("sections") if isinstance(fonts_blob, dict) else {},
    }
    try:
        normalised = normalise_payload(data)
    except ThemeError:
        normalised = default_payload()
    return normalised


def save_theme(
    db: Session,
    publication: PublicationProfile,
    payload: dict[str, Any],
    *,
    actor_id: int | None,
    name: str = "",
    notes: str = "",
) -> ThemeRevision:
    """Write a new immutable revision and make it current."""
    normalised = normalise_payload(payload)
    critical = [item for item in contrast_warnings(normalised) if item["level"] == "critical"]
    if critical:
        raise ThemeError(critical[0]["message"])
    if contrast_ratio(normalised["colors"]["ink"], normalised["colors"]["paper"]) < 3.0:
        raise ThemeError("Body ink and paper have insufficient contrast to print legibly.")
    previous = db.scalar(
        select(ThemeRevision)
        .where(ThemeRevision.publication_id == publication.id)
        .order_by(ThemeRevision.revision.desc())
        .limit(1)
    )
    revision = (previous.revision + 1) if previous else 1
    db.execute(
        ThemeRevision.__table__.update()
        .where(ThemeRevision.publication_id == publication.id)
        .values(is_current=False)
    )
    row = ThemeRevision(
        publication_id=publication.id,
        revision=revision,
        name=(name.strip()[:160] or initial_theme_name(publication)),
        is_current=True,
        colors_json=json.dumps(normalised["colors"], ensure_ascii=False),
        fonts_json=json.dumps(
            {
                "fonts": normalised["fonts"],
                "metrics": normalised["metrics"],
                "sections": normalised["sections"],
            },
            ensure_ascii=False,
        ),
        notes=(notes or "").strip()[:400],
        created_by=actor_id,
    )
    db.add(row)
    publication.accent = normalised["colors"]["accent"]
    publication.updated_at = utcnow()
    db.flush()
    # Every unpublished preview of this paper is now out of date. Published
    # editions keep their pinned snapshot and are never touched.
    from .models import Edition

    for edition in db.scalars(
        select(Edition).where(
            Edition.publication_id == publication.id,
            Edition.status != "published",
            Edition.preview_state == "current",
        )
    ):
        edition.preview_state = "stale"
    db.flush()
    return row


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _css_family(family: str) -> str:
    cleaned = _SAFE_CSS_FAMILY.sub("", family).strip() or "serif"
    return f'"{cleaned}"'


def _asset_alias(asset: FontAsset) -> str:
    family = _SAFE_CSS_FAMILY.sub("", asset.family or "Imported").strip() or "Imported"
    return f"CPF{asset.id} {family}"


def _font_format(name: str) -> str:
    return "opentype" if name.casefold().endswith(".otf") else "truetype"


def _select_fonts(payload: dict[str, Any], assets: list[FontAsset]) -> tuple[dict[str, dict], list[dict]]:
    """Resolve each role to a real face and report substitutions/coverage."""
    installed = discover_installed_fonts()
    resolved: dict[str, dict[str, Any]] = {}
    warnings: list[dict[str, str]] = []
    samples = role_samples()
    for role in FONT_ROLES:
        selection = payload["fonts"][role["key"]]
        face, notes = resolve_face(
            family=selection["family"],
            weight=int(selection["weight"]),
            style=selection["style"],
            asset_id=selection.get("asset_id"),
            assets=assets,
            installed=installed,
        )
        for note in notes:
            warnings.append(
                {"level": "warning", "code": f"font_{role['key']}", "message": f"{role['label']}: {note}"}
            )
        if face is None:
            resolved[role["key"]] = {
                "css_family": _css_family(selection["family"]),
                "stack": f"{_css_family(selection['family'])}, {selection['fallback']}",
                "weight": int(selection["weight"]),
                "style": selection["style"],
                "face": None,
            }
            continue
        if face.source == "asset" and face.asset_id is not None:
            asset = next((item for item in assets if item.id == face.asset_id), None)
            css_name = _css_family(_asset_alias(asset)) if asset is not None else _css_family(face.family)
        else:
            css_name = _css_family(face.family)
        resolved[role["key"]] = {
            "css_family": css_name,
            "stack": f"{css_name}, {selection['fallback']}",
            "weight": face.weight,
            "style": face.style,
            "face": face,
        }
        if face.unavailable:
            warnings.append(
                {
                    "level": "warning",
                    "code": f"font_unavailable_{role['key']}",
                    "message": f"{role['label']}: {face.reason or 'the selected font file is unavailable.'}",
                }
            )
            continue
        for sample_name in ("latin", "numeric", "punctuation"):
            report = glyph_coverage(face.path, samples[sample_name])
            if report.get("checked") and report.get("missing"):
                preview = "".join(dict.fromkeys(report["missing"]))[:12]
                warnings.append(
                    {
                        "level": "warning",
                        "code": f"glyphs_{role['key']}_{sample_name}",
                        "message": (
                            f"{role['label']}: {face.family} lacks {preview!r}; "
                            "the browser will substitute another family for those characters."
                        ),
                    }
                )
                break
        report = glyph_coverage(face.path, samples["urdu"])
        if report.get("checked") and report.get("missing"):
            warnings.append(
                {
                    "level": "note",
                    "code": f"urdu_{role['key']}",
                    "message": f"{role['label']}: no Urdu glyph coverage in {face.family}; Urdu falls back.",
                }
            )
    return resolved, warnings


def theme_warnings(payload: dict[str, Any], assets: list[FontAsset]) -> list[dict[str, str]]:
    _resolved, font_notes = _select_fonts(payload, assets)
    return [*contrast_warnings(payload), *font_notes]


def describe_theme(payload: dict[str, Any], assets: list[FontAsset]) -> dict[str, Any]:
    """Data for the appearance screen: resolved faces and role previews."""
    resolved, notes = _select_fonts(payload, assets)
    samples = role_samples()
    roles = []
    for role in FONT_ROLES:
        selection = payload["fonts"][role["key"]]
        info = resolved[role["key"]]
        roles.append(
            {
                "key": role["key"],
                "label": role["label"],
                "selection": selection,
                "stack": info["stack"],
                "weight": info["weight"],
                "style": info["style"],
                "sample": role["sample"],
                "urdu_sample": samples["urdu"],
                "available": info["face"] is not None,
                "face": info["face"].as_dict() if info["face"] is not None else None,
            }
        )
    return {
        "roles": roles,
        "warnings": [*contrast_warnings(payload), *notes],
        "palette": [dict(item) for item in COLOR_FIELDS],
    }


def _copy_referenced_fonts(
    payload: dict[str, Any], assets: list[FontAsset], target: Path
) -> None:
    from .config import settings

    target_dir = target / "fonts"
    for role in FONT_ROLES:
        asset_id = payload["fonts"][role["key"]].get("asset_id")
        if not asset_id:
            continue
        asset = next((item for item in assets if item.id == asset_id), None)
        if asset is None:
            continue
        source = target_dir / asset.stored_name
        if source.exists():
            continue
        original = settings.fonts_dir / asset.stored_name
        if not original.exists():
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, source)


def theme_css(
    payload: dict[str, Any],
    *,
    assets: list[FontAsset],
    font_dir: Path | None = None,
    font_url_prefix: str = "",
) -> tuple[str, list[dict[str, str]]]:
    """Compile a theme into CSS that the print/e-paper renderers inject.

    When ``font_dir`` is supplied, referenced imports are copied next to the
    artefact and referenced relatively, so a published edition stays
    reproducible even if the font library changes later.
    """
    resolved, warnings = _select_fonts(payload, assets)
    if font_dir is not None:
        _copy_referenced_fonts(payload, assets, font_dir)
    colors = payload["colors"]
    metrics = payload["metrics"]
    lines: list[str] = []
    used_assets: dict[int, FontAsset] = {}
    for role in FONT_ROLES:
        selection = payload["fonts"][role["key"]]
        asset_id = selection.get("asset_id")
        if asset_id:
            asset = next((item for item in assets if item.id == asset_id), None)
            if asset is not None:
                used_assets[asset.id] = asset
    for asset in used_assets.values():
        alias = _asset_alias(asset)
        if font_dir is not None:
            src = f"{font_url_prefix}fonts/{asset.stored_name}"
        else:
            from .config import settings

            src = (settings.fonts_dir / asset.stored_name).resolve().as_uri()
        lines.append(
            "@font-face {"
            f' font-family: "{alias}";'
            f' src: url("{src}") format("{_font_format(asset.stored_name)}");'
            f" font-weight: {int(asset.weight or 400)};"
            f" font-style: {asset.style or 'normal'};"
            " font-display: block;"
            " }"
        )
    hair = mix(colors["rule"], colors["paper"], 0.45)
    hairline = mix(colors["rule"], colors["paper"], 0.22)
    ink_soft = mix(colors["ink"], colors["paper"], 0.82)
    variables = {
        "ink": colors["ink"],
        "ink-soft": ink_soft,
        "muted": colors["muted"],
        "rule": colors["rule"],
        "hair": hair,
        "hairline": hairline,
        "paper": colors["paper"],
        "surface": colors["surface"],
        "accent": colors["accent"],
        "primary": colors["primary"],
        "secondary": colors["secondary"],
        "link": colors["link"],
        "font-display": resolved["display"]["stack"],
        "font-headline": resolved["headline"]["stack"],
        "font-section": resolved["section"]["stack"],
        "font-standfirst": resolved["standfirst"]["stack"],
        "font-body": resolved["body"]["stack"],
        "font-caption": resolved["caption"]["stack"],
        "font-numeric": resolved["numeric"]["stack"],
        "display-weight": str(resolved["display"]["weight"]),
        "display-style": resolved["display"]["style"],
        "headline-weight": str(resolved["headline"]["weight"]),
        "headline-style": resolved["headline"]["style"],
        "section-weight": str(resolved["section"]["weight"]),
        "section-style": resolved["section"]["style"],
        "standfirst-weight": str(resolved["standfirst"]["weight"]),
        "standfirst-style": resolved["standfirst"]["style"],
        "body-weight": str(resolved["body"]["weight"]),
        "body-style": resolved["body"]["style"],
        "caption-weight": str(resolved["caption"]["weight"]),
        "caption-style": resolved["caption"]["style"],
        "numeric-weight": str(resolved["numeric"]["weight"]),
        "numeric-style": resolved["numeric"]["style"],
        "body-size": f"{metrics['body_size_pt']}pt",
        "body-leading": str(metrics["body_leading"]),
        "headline-scale": str(metrics["headline_scale"]),
        "headline-tracking": f"{metrics['headline_tracking']}em",
        "display-scale": str(metrics["display_scale"]),
    }
    lines.append(":root { " + " ".join(f"--{key}: {value};" for key, value in variables.items()) + " }")
    for section, value in payload.get("sections", {}).items():
        lines.append(
            f'[data-section="{section}"] .kicker, [data-section="{section}"] .genre-label '
            f"{{ color: {value}; }}"
        )
        lines.append(f'[data-section="{section}"] .kicker {{ border-color: {value}; }}')
    return "\n".join(lines), warnings


def theme_manifest(theme: ThemeRevision | None, payload: dict[str, Any], assets: list[FontAsset]) -> dict[str, Any]:
    font_files = []
    for role in FONT_ROLES:
        asset_id = payload["fonts"][role["key"]].get("asset_id")
        if not asset_id:
            continue
        asset = next((item for item in assets if item.id == asset_id), None)
        if asset is None:
            continue
        font_files.append(
            {
                "role": role["key"],
                "family": asset.family,
                "style": asset.style,
                "weight": asset.weight,
                "sha256": asset.sha256,
                "file": asset.stored_name,
                "licence": asset.license_note,
            }
        )
    return {
        "schema": "channel_press.theme.v1",
        "revision_id": theme.id if theme is not None else None,
        "revision": theme.revision if theme is not None else 0,
        "name": theme.name if theme is not None else "Standard edition",
        "version": THEME_VERSION,
        "hash": payload_hash(payload),
        "colors": payload["colors"],
        "fonts": payload["fonts"],
        "metrics": payload["metrics"],
        "sections": payload["sections"],
        "font_files": font_files,
        "qr": {"ink": "#111111", "paper": "#ffffff", "note": "QR modules stay black on white for scan reliability."},
    }


def resolve_edition_theme(
    db: Session, publication: PublicationProfile, edition=None
) -> tuple[ThemeRevision | None, dict[str, Any], str]:
    """Pick the pinned theme snapshot or the publication's current revision."""
    if edition is not None:
        snapshot = (getattr(edition, "theme_snapshot_json", "") or "").strip()
        if snapshot and snapshot not in {"{}", ""}:
            try:
                data = json.loads(snapshot)
            except ValueError:
                data = {}
            try:
                payload = normalise_payload(data)
            except ThemeError:
                payload = default_payload()
            theme = db.get(ThemeRevision, edition.theme_revision_id) if edition.theme_revision_id else None
            return theme, payload, "pinned"
    theme = current_theme(db, publication)
    return theme, theme_payload(theme), "current"


def fonts_used_by_themes(db: Session, asset_id: int) -> list[str]:
    """Publications whose themes reference an imported font."""
    names: list[str] = []
    for theme in db.scalars(select(ThemeRevision)):
        try:
            blob = json.loads(theme.fonts_json or "{}")
        except ValueError:
            continue
        families = blob.get("fonts") if isinstance(blob, dict) else {}
        if not isinstance(families, dict):
            continue
        if any(
            isinstance(selection, dict) and selection.get("asset_id") == asset_id
            for selection in families.values()
        ):
            publication = db.get(PublicationProfile, theme.publication_id)
            if publication is not None and publication.name not in names:
                names.append(publication.name)
    return names


def theme_is_used_by_editions(db: Session, theme_id: int) -> bool:
    from .models import Edition

    return bool(
        db.scalar(select(Edition.id).where(Edition.theme_revision_id == theme_id).limit(1))
    )


def available_font_assets(db: Session) -> list[FontAsset]:
    return list(db.scalars(select(FontAsset).order_by(FontAsset.family.asc(), FontAsset.weight.asc())))


__all__ = [
    "COLOR_FIELDS",
    "COLOR_KEYS",
    "FONT_ROLES",
    "METRIC_DEFAULTS",
    "METRIC_LIMITS",
    "PRESETS",
    "THEME_VERSION",
    "ThemeError",
    "ThemeRender",
    "available_font_assets",
    "compile_payload",
    "compile_theme",
    "contrast_ratio",
    "contrast_warnings",
    "current_theme",
    "default_payload",
    "describe_theme",
    "fonts_used_by_themes",
    "normalise_hex",
    "normalise_payload",
    "payload_hash",
    "pin_edition_theme",
    "preset",
    "resolve_edition_theme",
    "save_theme",
    "theme_css",
    "theme_font_stacks",
    "theme_hash_for_publication",
    "theme_manifest",
    "theme_palette",
    "theme_payload",
    "theme_warnings",
]
