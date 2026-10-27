"""Font discovery, validation and the managed font library.

The application can only promise fonts it can actually enumerate and render:

* installed families discovered from the operating system's own font
  directories/registry;
* licensed ``.ttf``/``.otf``/``.ttc`` files imported by an editor and stored by
  content hash under ``data/fonts``.

Nothing here trusts a browser-supplied path or file name. Imported files are
parsed with fontTools before they are stored, and the stored name is derived
from a checksum, never from the upload.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import settings

MAX_FONT_BYTES = 12 * 1024 * 1024
ALLOWED_EXTENSIONS = {".ttf", ".otf", ".ttc"}
_FORMATS = {".ttf": "truetype", ".otf": "opentype", ".ttc": "truetype"}
_REQUIRED_TABLES = {"cmap", "head", "hhea", "hmtx", "maxp", "name"}
_URDU_SAMPLE = "\u0627\u0633\u0644\u0627\u0645 \u0622\u0628\u0627\u062f"
_SAFE_STEM = re.compile(r"[^A-Za-z0-9._-]+")
_CACHE_FILE = "font-cache.json"
_CACHE_VERSION = 2

_installed_cache: list[dict] | None = None
_coverage_cache: dict[tuple[str, float, str], dict] = {}


class FontError(ValueError):
    """A font file or selection was rejected."""


@dataclass
class FontFace:
    family: str
    style: str
    weight: int
    path: str = ""
    source: str = "system"
    face_count: int = 1
    variable: bool = False
    unavailable: bool = False
    reason: str = ""
    asset_id: int | None = None

    @property
    def key(self) -> str:
        if self.asset_id is not None:
            return f"asset:{self.asset_id}"
        return f"system:{self.family}|{self.style}|{self.weight}"

    def as_dict(self) -> dict:
        data = asdict(self)
        data["key"] = self.key
        return data


@dataclass
class FontCatalogEntry:
    family: str
    faces: list[FontFace] = field(default_factory=list)
    source: str = "system"
    unavailable: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "family": self.family,
            "source": self.source,
            "unavailable": self.unavailable,
            "reason": self.reason,
            "faces": [face.as_dict() for face in self.faces],
        }


def _fonts_dir() -> Path:
    path = settings.fonts_dir
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_path() -> Path:
    return settings.data_dir / _CACHE_FILE


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def _style_from_name(name: str, flags: int, weight: int) -> str:
    lowered = name.casefold()
    if "italic" in lowered or "oblique" in lowered:
        return "italic"
    if flags & 0x01:  # OS/2 fsSelection italic bit
        return "italic"
    return "normal"


def _weight_class(value: object) -> int:
    try:
        weight = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 400
    if 1 <= weight <= 9:  # some legacy fonts use the 1-9 scale
        return {1: 100, 2: 200, 3: 300, 4: 400, 5: 500, 6: 600, 7: 700, 8: 800, 9: 900}[weight]
    return max(100, min(900, weight))


def inspect_font_file(path: Path | str, *, asset_id: int | None = None) -> FontFace:
    """Read real family/style/weight metadata from a font file.

    Raises :class:`FontError` when the file is not a usable outline font.
    """
    path = Path(path)
    try:
        from fontTools.ttLib import TTFont, TTLibError
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise FontError("Font inspection is unavailable: install fonttools.") from exc
    try:
        with TTFont(str(path), lazy=True, fontNumber=0) as font:
            tables = set(font.keys())
            missing = _REQUIRED_TABLES - tables
            if missing:
                raise FontError("The file is missing required font tables: " + ", ".join(sorted(missing)))
            if "glyf" not in tables and "CFF " not in tables and "CFF2" not in tables:
                raise FontError("The file has no outline data (glyf/CFF).")
            names = font["name"]
            family = ""
            subfamily = ""
            for record in names.names:
                try:
                    value = record.toUnicode().strip()
                except Exception:  # noqa: BLE001, S112 - malformed name records are skipped
                    continue
                if value and (record.nameID == 16 or (record.nameID == 1 and not family)):
                    family = value
                elif value and (record.nameID == 17 or (record.nameID == 2 and not subfamily)):
                    subfamily = value
            weight = _weight_class(getattr(font["OS/2"], "usWeightClass", 400) if "OS/2" in tables else 400)
            flags = int(getattr(font["OS/2"], "fsSelection", 0)) if "OS/2" in tables else 0
            face_count = int(getattr(getattr(font, "reader", None), "numFonts", 1) or 1)
            variable = "fvar" in tables
            cmap = font.getBestCmap() or {}
            codepoints = set(cmap)
            if not any(0x41 <= code <= 0x5A for code in codepoints):
                raise FontError(
                    "The font has no basic Latin capitals; it is not usable for newspaper text."
                )
            family = family or path.stem
            style = _style_from_name(subfamily or path.stem, flags, weight)
    except FontError:
        raise
    except (TTLibError, OSError, ValueError, KeyError) as exc:  # type: ignore[union-attr]
        raise FontError(f"The file is not a readable TrueType/OpenType font: {type(exc).__name__}") from exc
    return FontFace(
        family=family[:160],
        style=style,
        weight=weight,
        path=str(path),
        source="asset" if asset_id is not None else "system",
        face_count=face_count,
        variable=variable,
        asset_id=asset_id,
    )


def _windows_font_files() -> list[Path]:
    files: list[Path] = []
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return files
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    ]
    directories = [
        Path(r"C:\Windows\Fonts"),
        Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts",
    ]
    for hive, key_name in roots:
        try:
            key = winreg.OpenKey(hive, key_name)
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    _name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                candidate = Path(str(value))
                if not candidate.is_absolute():
                    candidate = directories[0] / str(value)
                if candidate.suffix.casefold() in ALLOWED_EXTENSIONS:
                    files.append(candidate)
    for directory in directories:
        if directory.exists():
            files.extend(directory.glob("*.ttf"))
            files.extend(directory.glob("*.otf"))
            files.extend(directory.glob("*.ttc"))
    return files


def _portable_font_files() -> list[Path]:
    files: list[Path] = []
    directories = [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".fonts",
        Path.home() / ".local" / "share" / "fonts",
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
        Path.home() / "Library" / "Fonts",
    ]
    for directory in directories:
        if not directory.exists():
            continue
        for pattern in ("**/*.ttf", "**/*.otf", "**/*.ttc"):
            files.extend(directory.glob(pattern))
    return files


def _read_cache() -> list[dict] | None:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if payload.get("version") != _CACHE_VERSION:
        return None
    faces = payload.get("faces")
    return faces if isinstance(faces, list) else None


def _write_cache(faces: list[dict]) -> None:
    try:
        _cache_path().write_text(
            json.dumps({"version": _CACHE_VERSION, "faces": faces}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def discover_installed_fonts(*, force: bool = False) -> list[dict]:
    """Every compatible installed face, with real metadata.

    Results are cached per process and on disk (keyed by path/mtime) because a
    Windows font directory holds several hundred files and discovery must not
    run on every page request.
    """
    global _installed_cache
    if _installed_cache is not None and not force:
        return _installed_cache
    cached = None if force else _read_cache()
    cached_by_path = {
        str(item.get("path")): item for item in (cached or []) if item.get("path")
    }
    files = _windows_font_files() if sys.platform.startswith("win") else _portable_font_files()
    seen: set[str] = set()
    faces: list[dict] = []
    changed = cached is None
    for path in sorted({item.resolve() for item in files if item.exists()}, key=str):
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        previous = cached_by_path.get(key)
        if previous and abs(float(previous.get("mtime", -1)) - mtime) < 0.001:
            faces.append(dict(previous))
            continue
        changed = True
        try:
            face = inspect_font_file(path)
        except FontError as exc:
            faces.append(
                {
                    "family": path.stem,
                    "style": "normal",
                    "weight": 400,
                    "path": key,
                    "source": "system",
                    "face_count": 1,
                    "variable": False,
                    "unavailable": True,
                    "reason": str(exc)[:240],
                    "asset_id": None,
                    "mtime": mtime,
                }
            )
            continue
        payload = face.as_dict()
        payload["mtime"] = mtime
        faces.append(payload)
    if changed:
        _write_cache(faces)
    faces.sort(key=lambda item: (item["family"].casefold(), item["style"], item["weight"]))
    _installed_cache = faces
    return faces


def clear_discovery_cache() -> None:
    global _installed_cache
    _installed_cache = None
    coverage_cache_clear()


def coverage_cache_clear() -> None:
    _coverage_cache.clear()


# ---------------------------------------------------------------------------
# imported library
# ---------------------------------------------------------------------------


def sanitise_filename(name: str) -> str:
    stem = Path(name or "font").stem
    stem = _SAFE_STEM.sub("-", stem).strip(".-_") or "font"
    return stem[:60]


def validate_font_bytes(data: bytes, filename: str) -> tuple[FontFace, str]:
    """Validate an upload and return (metadata, sha256).

    Rejects oversized files, unsupported extensions, executables disguised as
    fonts and corrupt/malformed font data before anything is written.
    """
    if not data:
        raise FontError("The uploaded file is empty.")
    if len(data) > MAX_FONT_BYTES:
        raise FontError(f"Font files must be at most {MAX_FONT_BYTES // (1024 * 1024)} MB.")
    extension = Path(filename or "").suffix.casefold()
    if extension not in ALLOWED_EXTENSIONS:
        raise FontError("Only .ttf, .otf and .ttc font files are supported.")
    signature = data[:4]
    if signature not in {b"\x00\x01\x00\x00", b"OTTO", b"ttcf", b"true", b"typ1"}:
        raise FontError("The file does not have a TrueType/OpenType signature.")
    digest = hashlib.sha256(data).hexdigest()
    import tempfile

    with tempfile.NamedTemporaryFile(
        suffix=extension, dir=settings.data_dir, delete=False
    ) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    try:
        face = inspect_font_file(temporary)
    finally:
        temporary.unlink(missing_ok=True)
    face.path = ""
    face.source = "asset"
    return face, digest


def import_font(
    *,
    data: bytes,
    filename: str,
    license_note: str,
    license_acknowledged: bool,
    uploaded_by: int | None,
) -> dict:
    """Store a validated font under its content hash and return metadata."""
    if not license_acknowledged:
        raise FontError("Confirm that you are licensed to use and embed this font.")
    note = (license_note or "").strip()
    if note and len(note) > 400:
        raise FontError("The licence note is too long.")
    face, digest = validate_font_bytes(data, filename)
    extension = Path(filename).suffix.casefold()
    stored_name = f"{digest[:16]}-{sanitise_filename(filename)}{extension}"
    target = _fonts_dir() / stored_name
    if not target.exists():
        target.write_bytes(data)
    return {
        "family": face.family,
        "style": face.style,
        "weight": face.weight,
        "filename": Path(filename).name[:240],
        "stored_name": stored_name,
        "relative_path": stored_name,
        "sha256": digest,
        "size_bytes": len(data),
        "license_note": note,
        "license_acknowledged": True,
        "uploaded_by": uploaded_by,
    }


def asset_face(asset) -> FontFace:
    path = _fonts_dir() / asset.stored_name
    return FontFace(
        family=asset.family or Path(asset.stored_name).stem,
        style=asset.style or "normal",
        weight=int(asset.weight or 400),
        path=str(path),
        source="asset",
        asset_id=asset.id,
        unavailable=not path.exists(),
        reason="" if path.exists() else "The stored font file is missing.",
    )


def catalog(assets: Iterable) -> list[FontCatalogEntry]:
    """Group installed and imported faces into selectable families."""
    entries: dict[tuple[str, str], FontCatalogEntry] = {}
    for face in discover_installed_fonts():
        key = (face["family"], "system")
        entry = entries.setdefault(key, FontCatalogEntry(face["family"], source="system"))
        entry.faces.append(
            FontFace(
                family=face["family"],
                style=face.get("style", "normal"),
                weight=int(face.get("weight", 400)),
                path=face.get("path", ""),
                source="system",
                face_count=int(face.get("face_count", 1)),
                variable=bool(face.get("variable")),
                unavailable=bool(face.get("unavailable")),
                reason=face.get("reason", ""),
            )
        )
    imported: dict[str, FontCatalogEntry] = {}
    for asset in assets:
        face = asset_face(asset)
        entry = imported.setdefault(
            f"{face.family}|{face.asset_id}", FontCatalogEntry(face.family, source="asset")
        )
        entry.faces.append(face)
        entry.unavailable = entry.unavailable or face.unavailable
    rows = sorted(entries.values(), key=lambda item: item.family.casefold())
    rows.extend(sorted(imported.values(), key=lambda item: (item.family.casefold(), item.faces[0].asset_id or 0)))
    return rows


def resolve_face(
    *,
    family: str,
    weight: int,
    style: str,
    asset_id: int | None,
    assets: Iterable,
    installed: list[dict] | None = None,
) -> tuple[FontFace | None, list[str]]:
    """Find the closest real face in the chosen family.

    Returns the face and human-readable substitution notes. A requested weight
    that does not exist is never faked with synthetic bold: the nearest real
    face is used and the substitution is reported.
    """
    warnings: list[str] = []
    wanted_style = "italic" if style == "italic" else "normal"
    if asset_id is not None:
        for asset in assets:
            if asset.id == asset_id:
                face = asset_face(asset)
                if face.style != wanted_style or face.weight != int(weight):
                    warnings.append(
                        f"{face.family}: the selected file is {face.weight} {face.style}; "
                        f"the requested {weight} {wanted_style} face is not part of this import."
                    )
                return face, warnings
        return None, [f"The imported font asset {asset_id} no longer exists."]
    rows = installed if installed is not None else discover_installed_fonts()
    faces = [row for row in rows if row.get("family") == family]
    if not faces:
        return None, [f"Font family '{family}' is not installed on this computer."]
    same_style = [row for row in faces if row.get("style", "normal") == wanted_style]
    pool = same_style or faces
    if not same_style:
        warnings.append(f"{family} has no italic face; the upright face is used.")
    ordered = sorted(pool, key=lambda row: abs(int(row.get("weight", 400)) - int(weight)))
    chosen = ordered[0]
    if int(chosen.get("weight", 400)) != int(weight):
        warnings.append(
            f"{family}: no {weight} weight; using the {chosen.get('weight', 400)} face."
        )
    return (
        FontFace(
            family=chosen["family"],
            style=chosen.get("style", "normal"),
            weight=int(chosen.get("weight", 400)),
            path=chosen.get("path", ""),
            source="system",
            unavailable=bool(chosen.get("unavailable")),
            reason=chosen.get("reason", ""),
        ),
        warnings,
    )


# ---------------------------------------------------------------------------
# coverage
# ---------------------------------------------------------------------------


def glyph_coverage(path: str, text: str) -> dict:
    """Which characters of *text* the font can actually render."""
    if not path:
        return {"missing": list(text), "checked": False}
    key = (path, Path(path).stat().st_mtime if Path(path).exists() else 0.0, text)
    cached = _coverage_cache.get(key)
    if cached is not None:
        return cached
    try:
        from fontTools.ttLib import TTFont, TTLibError
    except ImportError:  # pragma: no cover
        return {"missing": [], "checked": False}
    try:
        with TTFont(path, lazy=True, fontNumber=0) as font:
            codepoints = set(font.getBestCmap() or {})
    except (TTLibError, OSError, ValueError):
        result = {"missing": list(text), "checked": False}
        _coverage_cache[key] = result
        return result
    missing = [char for char in text if not char.isspace() and ord(char) not in codepoints]
    result = {"missing": missing, "checked": True, "urdu": not any(ord(c) > 0x600 for c in missing)}
    _coverage_cache[key] = result
    return result


def role_samples() -> dict[str, str]:
    return {
        "latin": "The Quetta market reopened after the blast — officials said 12 people were hurt.",
        "numeric": "0123456789 · 34% · $1.2bn · 2026",
        "punctuation": "\u201cQuoted\u201d \u2018speech\u2019 \u2014 dash; colon: (brackets) & ampersand.",
        "urdu": _URDU_SAMPLE,
    }


def font_summary(faces: list[FontFace]) -> dict:
    return {
        "families": sorted({face.family for face in faces}),
        "imported": sorted({face.family for face in faces if face.source == "asset"}),
        "count": len(faces),
    }
