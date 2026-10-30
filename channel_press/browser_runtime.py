from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path


def is_frozen_application() -> bool:
    return bool(getattr(sys, "frozen", False))


def playwright_browsers_dir() -> Path:
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured:
        return Path(configured)
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def chromium_revision() -> str | None:
    if is_frozen_application():
        revisions = sorted(
            path.name.removeprefix("chromium-")
            for path in playwright_browsers_dir().glob("chromium-*")
            if path.is_dir()
        )
        return revisions[0] if len(revisions) == 1 else None
    package = importlib.util.find_spec("playwright")
    if package is None or not package.origin:
        return None
    manifest_path = Path(package.origin).parent / "driver" / "package" / "browsers.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for browser in manifest.get("browsers", []):
        if browser.get("name") == "chromium" and browser.get("revision") is not None:
            return str(browser["revision"])
    return None


def chromium_executable() -> Path | None:
    revision = chromium_revision()
    if revision is None:
        return None
    browser_dir = playwright_browsers_dir() / f"chromium-{revision}"
    platform_dirs = (
        ("chrome-win", "chrome-win64")
        if os.name == "nt"
        else ("chrome-linux", "chrome-linux64")
    )
    for platform_dir in platform_dirs:
        executable = browser_dir / platform_dir / "chrome.exe"
        if os.name != "nt":
            executable = browser_dir / platform_dir / "chrome"
        if executable.is_file():
            return executable
    return None


def system_browser_path() -> Path | None:
    candidates = [
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
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def print_browser_path() -> Path | None:
    if is_frozen_application():
        return chromium_executable()
    return chromium_executable() or system_browser_path()


def launch_print_browser(playwright, *, timeout_ms: int | None = None):
    options = {
        "headless": True,
        "args": ["--allow-file-access-from-files", "--disable-gpu"],
    }
    if timeout_ms is not None:
        options["timeout"] = timeout_ms

    if is_frozen_application():
        if chromium_executable() is None:
            raise RuntimeError(
                "The bundled Chromium print engine is missing or incomplete. "
                "Use Repair Installation before rendering."
            )
        try:
            return playwright.chromium.launch(**options)
        except Exception as exc:
            raise RuntimeError(
                "The bundled Chromium print engine could not start. "
                "Use Repair Installation and review Diagnostics."
            ) from exc

    browser_path = system_browser_path()
    if browser_path is not None:
        channel = "chrome" if browser_path.name.casefold() == "chrome.exe" else "msedge"
        return playwright.chromium.launch(channel=channel, **options)
    try:
        return playwright.chromium.launch(**options)
    except Exception as exc:
        raise RuntimeError(
            "No supported print browser is installed. Install the packaged renderer "
            "or a supported Chrome/Edge browser."
        ) from exc
