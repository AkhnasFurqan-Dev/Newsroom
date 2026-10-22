from __future__ import annotations

from datetime import UTC, datetime

import pytest

from channel_press.security import (
    csrf_valid,
    hash_password,
    needs_rehash,
    redact,
    safe_filename,
    sha256_text,
    token_digest,
    validate_public_http_url,
    verify_password,
)
from channel_press.timeutil import as_utc, iso_utc, local_boundary_utc, parse_datetime


def test_password_hash_round_trip_and_minimum_length():
    encoded = hash_password("a sufficiently long password")
    assert encoded.startswith("$argon2id$")
    assert verify_password(encoded, "a sufficiently long password")
    assert not verify_password(encoded, "wrong password")
    assert needs_rehash(encoded) is False
    with pytest.raises(ValueError):
        hash_password("short")


def test_session_and_csrf_tokens_are_compared_safely():
    token = "token-value"
    assert token_digest(token) == token_digest(token)
    assert token_digest(token) != token_digest(token + "x")
    assert csrf_valid("abc", "abc")
    assert not csrf_valid("abc", "abd")
    assert not csrf_valid("", "abc")


def test_redaction_removes_credentials_and_long_hex_strings():
    text = "password=hunter2 api_hash:abcdef token:xyz\nsecret=0123456789abcdef0123456789abcdef"
    cleaned = redact(text)
    assert "hunter2" not in cleaned
    assert "abcdef" not in cleaned
    assert "0123456789abcdef" not in cleaned
    assert "[redacted]" in cleaned


def test_safe_filename_strips_path_and_control_characters():
    assert safe_filename("../../etc/passwd") == "....etcpasswd"
    assert safe_filename("photo name?.png") == "photo_name.png"
    assert safe_filename("") == "upload"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "file:///C:/Windows/win.ini",
        "ftp://example.com/file",
        "https://user:pass@example.com/",
    ],
)
def test_ssrf_guard_rejects_local_and_non_http_targets(url):
    ok, _reason = validate_public_http_url(url)
    assert ok is False


def test_ssrf_guard_accepts_public_literal_ip_and_honors_allowlist():
    ok, reason = validate_public_http_url("https://93.184.216.34/report")
    assert ok is True, reason
    ok, _reason = validate_public_http_url("https://93.184.216.34/report", {"example.com"})
    assert ok is False


def test_datetime_helpers_use_utc_and_named_zones():
    naive = datetime(2026, 10, 20, 5, 0)
    assert as_utc(naive) == datetime(2026, 10, 20, 5, 0, tzinfo=UTC)
    assert parse_datetime("2026-10-20T05:00:00+05:00") == datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    cutoff = local_boundary_utc(datetime(2026, 10, 20).date(), datetime.min.time().replace(hour=5), "Asia/Karachi")
    assert cutoff == datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
    assert iso_utc(cutoff) == "2026-10-20T00:00:00Z"


def test_sha256_text_is_stable():
    assert sha256_text("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
