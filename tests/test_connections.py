from __future__ import annotations

import importlib

import httpx

from channel_press.preflight import Check, PreflightReport, internet_check
from channel_press.telegram.transport import TelegramTransportError

web_app = importlib.import_module("channel_press.web.app")


def test_connections_page_shows_current_readiness_and_manual_import(client, owner, auth, monkeypatch):
    report = PreflightReport(
        checks=[Check("renderer", "Print renderer", "ok", "Bundled renderer is ready.")],
        telegram={"status": "missing"},
        ollama={"detail": "Qwen3.5 Standard passed its local draft test."},
    )
    monkeypatch.setattr(web_app, "build_preflight", lambda **_kwargs: report)
    monkeypatch.setattr(
        web_app,
        "internet_check",
        lambda: Check("internet", "Internet access", "ok", "HTTPS is available."),
    )

    response = client.get("/connections")

    assert response.status_code == 200
    assert "Check again" in response.text
    assert "Telegram API credentials are not configured" in response.text
    assert "Import rights-cleared posts" in response.text
    assert "Qwen3.5 Standard passed its local draft test." in response.text
    assert "Complete first-run setup" in response.text
    assert 'href="/ai-models"' in response.text
    assert 'href="/branding"' in response.text
    assert 'href="/sources"' in response.text
    assert 'href="/schedules"' in response.text
    assert 'href="/build"' in response.text


def test_internet_check_reports_bounded_timeout(monkeypatch):
    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            raise httpx.ReadTimeout("request timed out")

    monkeypatch.setattr("channel_press.preflight.httpx.Client", Client)

    result = internet_check(timeout_seconds=0.01)

    assert result.key == "internet"
    assert result.status == "warn"
    assert "timed out" in result.detail
    assert "proxy" in result.action


def test_telegram_timeout_mentions_possible_restriction_without_overstating():
    message = web_app._telegram_error(TelegramTransportError("Telegram request timed out."))

    assert "may be restricted by your ISP or region" in message
    assert "Rights-cleared manual import remains available." in message


def test_telegram_auth_error_is_not_classified_as_network_blocking():
    message = web_app._telegram_error(TelegramTransportError("Telegram session is unauthorized."))

    assert message == "Telegram session is unauthorized."
    assert "restricted by your ISP" not in message
