"""Local preflight checks.

Every check is designed to be safe to display to an owner: it reports presence,
reachability and capability, never credential values. Diagnostics never include
the API hash, the API id, the session string, verification codes or 2FA
passwords. The Telegram gateway itself only exposes booleans and Telegram error
classes.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .config import settings

OK = "ok"
WARN = "warn"
MISSING = "missing"
ERROR = "error"
BLOCKED = "blocked"

SECRET_ENV_KEYS = (
    "CHANNEL_PRESS_TELEGRAM_API_HASH",
    "CHANNEL_PRESS_TELEGRAM_API_ID",
    "CHANNEL_PRESS_SECRET_KEY",
)


@dataclass
class Check:
    key: str
    label: str
    status: str
    detail: str
    action: str = ""
    required_for: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "detail": self.detail,
            "action": self.action,
            "required_for": self.required_for,
        }


@dataclass
class PreflightReport:
    checks: list[Check] = field(default_factory=list)
    telegram: dict = field(default_factory=dict)
    ollama: dict = field(default_factory=dict)

    @property
    def blocking(self) -> list[Check]:
        return [check for check in self.checks if check.status in {MISSING, ERROR, BLOCKED}]

    def as_dict(self) -> dict:
        return {
            "checks": [check.as_dict() for check in self.checks],
            "telegram": self.telegram,
            "ollama": self.ollama,
            "ready_for_fixture": not self.blocking,
        }


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def chrome_path() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    return next((path for path in candidates if path.exists()), None)


def _session_permissions_ok(path: Path) -> tuple[bool, str]:
    if os.name != "nt":
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            return False, f"Session file mode {oct(mode)} is group/world readable."
        return True, "Restrictive POSIX permissions."
    return True, "Windows ACLs apply; file lives in the ignored data directory."


def telegram_environment() -> dict:
    """Non-secret description of Telegram readiness.

    ``status`` is one of ``missing``, ``configured``, ``session_present`` or
    ``session_unreadable``. Authentication itself is only known after the
    gateway connects, so the caller adds ``authenticated``.
    """
    api_id = bool(settings.telegram_api_id.strip())
    api_hash = bool(settings.telegram_api_hash.strip())
    session = settings.telegram_session_file
    session_exists = session.exists() and session.stat().st_size > 0
    state = {
        "api_id_configured": api_id,
        "api_hash_configured": api_hash,
        "credentials_configured": api_id and api_hash,
        "telethon_installed": module_available("telethon"),
        "session_path_inside_data": _is_within(session, settings.data_dir),
        "session_present": session_exists,
        "session_readable": False,
        "username_configured": bool(settings.telegram_source_username.strip()),
        "status": MISSING,
    }
    if session_exists:
        try:
            state["session_readable"] = session.stat().st_size > 0
            ok, note = _session_permissions_ok(session)
            state["session_permissions"] = note
            state["session_permissions_ok"] = ok
        except OSError:
            state["session_permissions_ok"] = False
    if not state["credentials_configured"]:
        state["status"] = MISSING
    elif not state["telethon_installed"]:
        state["status"] = BLOCKED
    elif session_exists:
        state["status"] = "session_present"
    else:
        state["status"] = "configured"
    return state


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def ollama_environment(*, probe: bool = True) -> dict:
    state = {
        "base_url": settings.ollama_base_url,
        "model": settings.ollama_model,
        "reachable": False,
        "installed_models": [],
        "model_present": False,
        "detail": "",
    }
    if not probe:
        state["detail"] = "Probe skipped."
        return state
    try:
        import httpx

        with httpx.Client(timeout=3) as client:
            response = client.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
        state["reachable"] = response.status_code == 200
        if response.status_code == 200:
            names = [str(item.get("name")) for item in response.json().get("models", [])]
            state["installed_models"] = sorted(names)
            wanted = settings.ollama_model
            state["model_present"] = wanted in names or any(
                name.split(":")[0] == wanted.split(":")[0] for name in names
            )
            state["detail"] = (
                f"Model {wanted} is installed."
                if state["model_present"]
                else f"Ollama is running but {wanted} is not installed (ollama pull {wanted})."
            )
        else:
            state["detail"] = f"Ollama returned HTTP {response.status_code}."
    except Exception as exc:  # noqa: BLE001 - offline is an expected state
        state["detail"] = f"Ollama is not reachable ({type(exc).__name__})."
    return state


def database_check() -> Check:
    url = settings.effective_database_url
    if url.startswith("sqlite"):
        path = Path(url.split("///", 1)[-1])
        if not path.exists():
            return Check("database", "Database", MISSING, "Database file does not exist yet.", "Run setup.")
        try:
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
                connection.execute("SELECT 1 FROM users LIMIT 1")
            return Check("database", "Database", OK, f"{path.name} readable (WAL SQLite).")
        except sqlite3.Error as exc:
            return Check("database", "Database", ERROR, f"Database error: {type(exc).__name__}.", "Check the file lock.")
    return Check("database", "Database", OK, "Managed by the configured database URL.")


def disk_check() -> Check:
    try:
        usage = shutil.disk_usage(settings.data_dir)
        free_mb = usage.free // (1024 * 1024)
        status = OK if free_mb > 512 else WARN
        return Check(
            "disk",
            "Free disk space",
            status,
            f"{free_mb} MB free in the data volume (quota target {settings.disk_quota_mb} MB).",
            "Free space before a large video import." if status is WARN else "",
        )
    except OSError as exc:
        return Check("disk", "Free disk space", WARN, f"Could not read disk usage ({type(exc).__name__}).")


def build_preflight(*, probe_ollama: bool = True) -> PreflightReport:
    report = PreflightReport()
    report.checks.append(database_check())
    report.checks.append(disk_check())

    chrome = chrome_path()
    report.checks.append(
        Check(
            "chrome",
            "Print browser",
            OK if chrome else MISSING,
            str(chrome) if chrome else "Chrome or Edge was not found; PDF rendering will fail.",
            "" if chrome else "Install Google Chrome.",
            "PDF and page proofs",
        )
    )
    report.checks.append(
        Check(
            "ffmpeg",
            "FFmpeg (optional)",
            OK if shutil.which("ffmpeg") else WARN,
            shutil.which("ffmpeg") or "Not installed; video stills use the OpenCV fallback.",
        )
    )
    report.checks.append(
        Check(
            "opencv",
            "OpenCV (video stills)",
            OK if module_available("cv2") else WARN,
            "Installed." if module_available("cv2") else "Not installed; video still extraction unavailable.",
        )
    )
    report.checks.append(
        Check(
            "playwright",
            "Paged.js renderer",
            OK if module_available("playwright") else MISSING,
            "Playwright is installed." if module_available("playwright") else "Playwright is missing.",
        )
    )
    report.checks.append(
        Check(
            "pymupdf",
            "PDF inspection",
            OK if module_available("fitz") else MISSING,
            "PyMuPDF available for geometry QA." if module_available("fitz") else "PyMuPDF is missing.",
        )
    )

    telegram = telegram_environment()
    report.telegram = telegram
    if not telegram["telethon_installed"]:
        telegram_check = Check(
            "telegram",
            "Telegram live adapter",
            BLOCKED,
            "Telethon is not installed in this environment.",
            "Run pip install -e \".[telegram]\".",
            "Live source retrieval",
        )
    elif not telegram["credentials_configured"]:
        telegram_check = Check(
            "telegram",
            "Telegram live adapter",
            MISSING,
            "API id/hash are not configured in .env. Fixture and direct import still work.",
            "Edit .env locally, then reload the app.",
            "Live source retrieval",
        )
    elif not telegram["session_present"]:
        telegram_check = Check(
            "telegram",
            "Telegram live adapter",
            WARN,
            "Credentials are present; no authorized session yet. Complete the local login.",
            "Open Source desk → Telegram connection.",
            "Live source retrieval",
        )
    else:
        telegram_check = Check(
            "telegram",
            "Telegram live adapter",
            OK,
            "Credentials and a local session file are present. Use Test source to confirm the session.",
            "",
            "Live source retrieval",
        )
    report.checks.append(telegram_check)

    ollama = ollama_environment(probe=probe_ollama)
    report.ollama = ollama
    report.checks.append(
        Check(
            "ollama",
            "Local model",
            OK if ollama["model_present"] else (WARN if ollama["reachable"] else MISSING),
            ollama["detail"],
            "" if ollama["model_present"] else f"Install Ollama and run: ollama pull {settings.ollama_model}",
            "AI drafting",
        )
    )
    report.checks.append(
        Check(
            "public_url",
            "Public source list (optional)",
            OK if settings.public_base_url else WARN,
            settings.public_base_url or "Not configured; printed QRs stay in the truthful source appendix mode.",
            "Set CHANNEL_PRESS_PUBLIC_BASE_URL only when a hosted source list really exists.",
            "Hosted QR destinations",
        )
    )
    return report
