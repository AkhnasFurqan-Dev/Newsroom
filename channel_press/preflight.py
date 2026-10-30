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
import ssl
import socket
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from sqlalchemy.exc import OperationalError

from .browser_runtime import print_browser_path
from .config import settings

OK = "ok"
WARN = "warn"
MISSING = "missing"
ERROR = "error"
BLOCKED = "blocked"
CONFIGURED = "configured"
SESSION_PRESENT = "session_present"

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
    """Is ``name`` importable in this process."""
    return importlib.util.find_spec(name) is not None


def chrome_path() -> Path | None:
    return print_browser_path()


def webview2_runtime_path() -> Path | None:
    if os.name != "nt":
        return None
    roots = [
        Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"))
        / "Microsoft"
        / "EdgeWebView"
        / "Application",
        Path(os.environ.get("PROGRAMFILES", "C:\\Program Files"))
        / "Microsoft"
        / "EdgeWebView"
        / "Application",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft"
        / "EdgeWebView"
        / "Application",
    ]
    for root in roots:
        try:
            versions = sorted(
                (path for path in root.iterdir() if path.is_dir()),
                key=lambda path: tuple(
                    int(part) if part.isdigit() else -1 for part in path.name.split(".")
                ),
                reverse=True,
            )
        except OSError:
            continue
        for version in versions:
            runtime = version / "msedgewebview2.exe"
            if runtime.is_file():
                return runtime
    return None


def internet_check(*, timeout_seconds: float = 4.0) -> Check:
    try:
        with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
            with client.stream(
                "GET",
                "https://www.microsoft.com/",
                headers={"Range": "bytes=0-0"},
            ) as response:
                status_code = response.status_code
        if status_code < 500:
            return Check("internet", "Internet access", OK, "An HTTPS connection succeeded.")
        return Check(
            "internet",
            "Internet access",
            WARN,
            f"The HTTPS endpoint returned HTTP {status_code}.",
            "Check the network proxy or captive-portal sign-in, then retry.",
        )
    except httpx.TimeoutException:
        return Check(
            "internet",
            "Internet access",
            WARN,
            "The HTTPS connection timed out.",
            "Check the internet connection or configured proxy, then retry.",
        )
    except httpx.ProxyError:
        return Check(
            "internet",
            "Internet access",
            WARN,
            "The configured network proxy could not complete the HTTPS request.",
            "Check the proxy settings and retry.",
        )
    except httpx.ConnectError as exc:
        cause = exc.__cause__ or exc
        if isinstance(cause, ssl.SSLError):
            detail = "The HTTPS certificate could not be verified."
            action = "Check the system date, certificate store or network proxy."
        elif isinstance(cause, socket.gaierror):
            detail = "The endpoint name could not be resolved."
            action = "Check DNS settings or the configured network proxy."
        else:
            detail = "An internet connection could not be established."
            action = "Check the internet connection or configured proxy, then retry."
        return Check("internet", "Internet access", WARN, detail, action)
    except httpx.HTTPError as exc:
        return Check(
            "internet",
            "Internet access",
            WARN,
            f"The HTTPS check failed ({type(exc).__name__}).",
            "Check the connection or proxy, then retry.",
        )


def _session_permissions_ok(path: Path) -> tuple[bool, str]:
    if os.name != "nt":
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            return False, f"Session file mode {oct(mode)} is group/world readable."
        return True, "Restrictive POSIX permissions."
    return True, "Windows ACLs apply; file lives in the ignored data directory."


def telegram_environment() -> dict:
    """Non-secret description of Telegram readiness.

    ``status`` is one of ``missing``, ``configured``, ``session_present``,
    ``session_unreadable`` or ``blocked``. Authentication itself is only known
    after the gateway connects, so the caller adds ``authenticated``.

    NOTE: monkeypatching ``module_available`` during testing may not reliably
    change the result when the test suite shares the same interpreter process
    with other tests that have already imported the module. Prefer a test design
    that uses a separate interpreter or that exercises the status logic through
    ``build_preflight`` / ``telegram_check`` so the path is fully re-derived.
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
    elif not session_exists:
        state["status"] = CONFIGURED
    elif not state.get("session_readable", False):
        state["status"] = "session_unreadable"
    else:
        state["status"] = SESSION_PRESENT
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
        "model_digest": "",
        "smoke_tested": False,
        "model_ready": False,
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
            models = {
                str(item.get("name") or ""): item
                for item in response.json().get("models", [])
                if isinstance(item, dict)
            }
            names = list(models)
            state["installed_models"] = sorted(names)
            wanted = settings.ollama_model
            model = models.get(wanted)
            if model is None and ":" not in wanted:
                model = models.get(f"{wanted}:latest")
            profile = None
            try:
                from .ai.model_profiles import profile_for_model

                profile = profile_for_model(wanted)
            except (OSError, ValueError):
                profile = None
            if model and profile:
                expected_digest = profile["registry_manifest_digest"].removeprefix("sha256:")
                installed_digest = str(model.get("digest") or "").removeprefix("sha256:")
                state["model_present"] = installed_digest == expected_digest
            else:
                state["model_present"] = bool(model)
            if model and state["model_present"]:
                state["model_digest"] = str(model.get("digest") or "")
                smoke_error = ""
                try:
                    from .ai.model_manager import model_smoke_record
                    from .db import SessionLocal

                    with SessionLocal() as db:
                        smoke = model_smoke_record(db, wanted, state["model_digest"])
                    state["smoke_tested"] = smoke is not None
                except (OperationalError, sqlite3.Error, OSError) as exc:
                    state["smoke_tested"] = False
                    smoke_error = type(exc).__name__
                state["model_ready"] = state["smoke_tested"] and bool(state["model_digest"])
                if smoke_error:
                    state["detail"] = (
                        f"Model {wanted} is installed, but local test status could not be "
                        f"read ({smoke_error})."
                    )
                elif state["smoke_tested"]:
                    state["detail"] = f"Model {wanted} passed its local draft test."
                else:
                    state["detail"] = f"Model {wanted} is installed; run its local draft test in AI Models."
            elif model:
                state["detail"] = (
                    f"Installed {wanted} does not match the reviewed model digest; "
                    "repair it from AI Models."
                )
            else:
                state["detail"] = (
                    f"Ollama is running but {wanted} is not installed. Use AI Models to install it."
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

    webview2 = webview2_runtime_path()
    report.checks.append(
        Check(
            "webview2",
            "Windows desktop runtime",
            OK if webview2 else (MISSING if os.name == "nt" else WARN),
            (
                f"WebView2 Runtime {webview2.parent.name} is installed."
                if webview2
                else (
                    "WebView2 Runtime was not found."
                    if os.name == "nt"
                    else "WebView2 is only required by the Windows desktop build."
                )
            ),
            (
                "Install the Microsoft Edge WebView2 Evergreen Runtime and restart Channel Press."
                if os.name == "nt" and not webview2
                else ""
            ),
            "Windows desktop shell",
        )
    )

    chrome = chrome_path()
    report.checks.append(
        Check(
            "chrome",
            "Print browser",
            OK if chrome else MISSING,
            str(chrome) if chrome else "No matching Playwright Chromium, Chrome or Edge was found.",
            "" if chrome else "Repair the bundled print renderer or install a supported browser.",
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
    report.checks.append(
        Check(
            "fonttools",
            "Font inspection",
            OK if module_available("fontTools") else WARN,
            "fontTools available for installed-font discovery and uploads."
            if module_available("fontTools")
            else "fontTools is missing; only fonts already installed can be listed by name and uploads are rejected.",
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
            OK if ollama["model_ready"] else (WARN if ollama["reachable"] else MISSING),
            ollama["detail"],
            "" if ollama["model_ready"] else "Open AI Models to install and test the selected local model.",
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
