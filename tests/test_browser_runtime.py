from __future__ import annotations

import pytest

from channel_press import browser_runtime


def test_chromium_path_uses_playwright_manifest_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(browser_runtime, "chromium_revision", lambda: "1148")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    executable = tmp_path / "chromium-1148" / "chrome-win" / "chrome.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()

    assert browser_runtime.chromium_executable() == executable


def test_frozen_renderer_uses_bundled_chromium_only(monkeypatch, tmp_path):
    calls = []

    class Chromium:
        @staticmethod
        def launch(**kwargs):
            calls.append(kwargs)
            return "bundled-browser"

    class Playwright:
        chromium = Chromium()

    executable = tmp_path / "chromium-1148" / "chrome-win" / "chrome.exe"
    monkeypatch.setattr(browser_runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(browser_runtime, "chromium_executable", lambda: executable)

    assert browser_runtime.launch_print_browser(Playwright()) == "bundled-browser"
    assert calls == [
        {
            "headless": True,
            "args": ["--allow-file-access-from-files", "--disable-gpu"],
        }
    ]


def test_frozen_renderer_reports_missing_bundled_browser(monkeypatch):
    class Chromium:
        @staticmethod
        def launch(**_kwargs):
            raise AssertionError("A missing bundled browser must not fall back to system Chrome.")

    class Playwright:
        chromium = Chromium()

    monkeypatch.setattr(browser_runtime.sys, "frozen", True, raising=False)
    monkeypatch.setattr(browser_runtime, "chromium_executable", lambda: None)

    with pytest.raises(RuntimeError, match="Repair Installation"):
        browser_runtime.launch_print_browser(Playwright())


def test_renderer_diagnostics_require_matching_browser_revision(monkeypatch, tmp_path):
    monkeypatch.setattr(browser_runtime, "chromium_revision", lambda: "1148")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(browser_runtime, "system_browser_path", lambda: None)

    assert browser_runtime.print_browser_path() is None

    executable = tmp_path / "chromium-1148" / "chrome-win" / "chrome.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    assert browser_runtime.print_browser_path() == executable
