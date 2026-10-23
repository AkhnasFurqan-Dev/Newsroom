"""Preflight diagnostics must be useful without ever revealing secrets."""

from __future__ import annotations

from pathlib import Path

from channel_press import preflight
from channel_press.config import settings

SECRET_ID = "9876543"
SECRET_HASH = "super-secret-api-hash-value"
SECRET_KEY = "super-secret-application-key"


def test_telegram_environment_reports_presence_not_values(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "telegram_api_id", SECRET_ID)
    monkeypatch.setattr(settings, "telegram_api_hash", SECRET_HASH)
    session = settings.data_dir / "telegram.session"
    session.write_bytes(b"fake-session-material")
    monkeypatch.setattr(settings, "telegram_session_path", str(session))

    state = preflight.telegram_environment()
    blob = str(state)
    assert state["api_id_configured"] is True
    assert state["api_hash_configured"] is True
    assert state["session_present"] is True
    assert state["session_path_inside_data"] is True
    assert state["status"] == "session_present"
    assert SECRET_ID not in blob
    assert SECRET_HASH not in blob
    assert "fake-session-material" not in blob


def test_missing_credentials_are_reported_as_missing(monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_id", "")
    monkeypatch.setattr(settings, "telegram_api_hash", "")
    state = preflight.telegram_environment()
    assert state["status"] == "missing"
    assert state["credentials_configured"] is False


def test_missing_telethon_blocks_only_the_live_adapter(monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_id", SECRET_ID)
    monkeypatch.setattr(settings, "telegram_api_hash", SECRET_HASH)
    monkeypatch.setattr(preflight, "module_available", lambda name: False if name == "telethon" else True)
    state = preflight.telegram_environment()
    assert state["status"] == "blocked"
    assert state["telethon_installed"] is False


def test_session_outside_the_data_directory_is_flagged(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "telegram_api_id", SECRET_ID)
    monkeypatch.setattr(settings, "telegram_api_hash", SECRET_HASH)
    outside = tmp_path / "elsewhere.session"
    outside.write_bytes(b"x")
    monkeypatch.setattr(settings, "telegram_session_path", str(outside))
    state = preflight.telegram_environment()
    assert state["session_present"] is True
    assert state["session_path_inside_data"] is False


def test_full_report_never_contains_secret_values(monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_id", SECRET_ID)
    monkeypatch.setattr(settings, "telegram_api_hash", SECRET_HASH)
    monkeypatch.setattr(settings, "secret_key", SECRET_KEY)
    report = preflight.build_preflight(probe_ollama=False)
    payload = report.as_dict()
    blob = str(payload) + str(report.checks) + str(report.telegram) + str(report.ollama)

    assert SECRET_ID not in blob
    assert SECRET_HASH not in blob
    assert SECRET_KEY not in blob
    assert isinstance(payload["ready_for_fixture"], bool)


def test_report_exposes_actionable_check_keys(monkeypatch):
    report = preflight.build_preflight(probe_ollama=False)
    keys = {check.key for check in report.checks}
    assert {"database", "disk", "chrome", "playwright", "telegram", "ollama", "public_url"} <= keys
    assert report.ollama["detail"] == "Probe skipped."
    for check in report.checks:
        assert check.status in {"ok", "warn", "missing", "error", "blocked"}
        if check.status in {"missing", "error", "blocked"}:
            assert check.action, f"{check.key} needs a user-actionable next step"


def test_database_check_reads_the_test_database():
    check = preflight.database_check()
    assert check.key == "database"
    assert check.status == "ok", check.detail


def test_diagnostics_page_does_not_leak_configured_secrets(client, owner, auth, monkeypatch):
    monkeypatch.setattr(settings, "telegram_api_id", SECRET_ID)
    monkeypatch.setattr(settings, "telegram_api_hash", SECRET_HASH)
    monkeypatch.setattr(settings, "secret_key", SECRET_KEY)

    response = client.get("/diagnostics")
    assert response.status_code == 200
    body = response.text
    assert SECRET_ID not in body
    assert SECRET_HASH not in body
    assert SECRET_KEY not in body


def test_preflight_session_path_default_is_inside_the_project_data(monkeypatch):
    monkeypatch.setattr(settings, "telegram_session_path", "./data/telegram.session")
    path = settings.telegram_session_file
    assert path.is_absolute()
    assert path.name == "telegram.session"
    assert isinstance(path, Path)
