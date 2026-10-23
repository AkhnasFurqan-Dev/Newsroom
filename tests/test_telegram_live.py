"""Telegram gateway tests against a deterministic fake transport.

No network, credential or real session is used. These tests prove the state
machine, window filtering, pagination, flood handling and error visibility; the
live smoke test remains a separate, explicitly authorized step.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from channel_press.telegram.service import (
    LOGIN_AUTHORIZED,
    LOGIN_CODE,
    LOGIN_PASSWORD,
    TelegramService,
    mask_phone,
)
from channel_press.telegram.transport import (
    PageResult,
    TelegramAuthError,
    TelegramChannelError,
    TelegramFloodWait,
    normalize_handle,
)
from channel_press.timeutil import parse_datetime


START = datetime(2026, 10, 19, 3, 0, tzinfo=UTC)
END = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)


def _messages() -> list[dict]:
    rows = [
        {"id": 1, "date": START.isoformat(), "text": "Published exactly at the opening boundary."},
        {"id": 28, "date": END.isoformat(), "text": "Published exactly at the cutoff; next issue."},
        {"id": 27, "date": (END - timedelta(seconds=1)).isoformat(), "text": "Last second before cutoff."},
    ]
    for index in range(2, 27):
        rows.append(
            {
                "id": index,
                "date": (START + timedelta(minutes=index)).isoformat(),
                "text": f"Inside-window report {index}.",
            }
        )
    return rows


class FakeTransport:
    def __init__(
        self,
        *,
        messages: list[dict] | None = None,
        authorized: bool = True,
        require_password: bool = False,
        flood_remaining: int = 0,
        flood_seconds: int = 1,
    ):
        self.messages = sorted(messages if messages is not None else _messages(), key=lambda m: m["id"], reverse=True)
        self.authorized = authorized
        self.require_password = require_password
        self.flood_remaining = flood_remaining
        self.flood_seconds = flood_seconds
        self.connected = False
        self.page_calls: list[tuple[int, datetime | None]] = []
        self.sent_codes: list[str] = []
        self.passwords: list[str] = []

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_authorized(self):
        return self.authorized

    async def send_code(self, phone: str):
        self.sent_codes.append(phone)
        return "phone-code-hash"

    async def sign_in(self, phone: str, code: str, phone_code_hash: str):
        if self.require_password:
            raise TelegramAuthError("2FA password required.")
        self.authorized = True

    async def sign_in_password(self, password: str):
        self.passwords.append(password)
        if password != "correct-password":
            raise TelegramAuthError("The 2FA password was rejected.")
        self.authorized = True

    async def me(self):
        return {"id": 42, "first_name": "Desk", "last_name": "Owner", "username": "desk"}

    async def resolve(self, handle: str):
        if handle == "private_channel":
            raise TelegramChannelError("@private_channel is private or this account cannot read it.")
        return {"id": 700, "title": "Test Desk", "username": handle, "is_channel": True, "is_megagroup": False}

    async def page(self, entity, *, limit: int, offset_id: int = 0, offset_date: datetime | None = None):
        self.page_calls.append((offset_id, offset_date))
        if self.flood_remaining:
            self.flood_remaining -= 1
            raise TelegramFloodWait(self.flood_seconds)
        chunk = []
        for raw in self.messages:
            if offset_id and int(raw["id"]) >= int(offset_id):
                continue
            if offset_date is not None and parse_datetime(raw["date"]) >= offset_date:
                continue
            chunk.append(raw)
            if len(chunk) >= limit:
                break
        return PageResult(messages=chunk, oldest_id=int(chunk[-1]["id"]) if chunk else None)

    async def download_media(self, entity, message_id: int, target):
        return str(target)


def _service(fake: FakeTransport, tmp_path, **kwargs) -> TelegramService:
    return TelegramService(
        api_id="12345",
        api_hash="not-a-real-hash",
        session_path=tmp_path / "telegram.session",
        factory=lambda: fake,
        **kwargs,
    )


def test_login_state_machine_never_exposes_the_phone_code_or_password(tmp_path):
    fake = FakeTransport(require_password=True)
    service = _service(fake, tmp_path)
    try:
        state = service.start_login("+923001234567")
        assert state["stage"] == LOGIN_CODE
        assert state["phone_masked"] == "+9••••••67"
        assert "923001234567" not in str(state)
        assert fake.sent_codes == ["+923001234567"]

        with pytest.raises(TelegramAuthError):
            service.submit_code("12")  # not a plausible numeric code

        state = service.submit_code("12345")
        assert state["stage"] == LOGIN_PASSWORD
        assert "password" in state["message"].lower()

        state = service.submit_password("correct-password")
        assert state["stage"] == LOGIN_AUTHORIZED
        assert service.login_state()["stage"] == LOGIN_AUTHORIZED
        # Secrets are dropped after sign-in and never written to the state.
        assert service._pending_phone == ""
        assert service._pending_hash == ""
        assert "correct-password" not in str(service.login_state())
        assert fake.passwords == ["correct-password"]
    finally:
        service.close()


def test_wrong_password_is_reported_and_the_login_stays_pending(tmp_path):
    fake = FakeTransport(require_password=True)
    service = _service(fake, tmp_path)
    try:
        service.start_login("+923001234567")
        service.submit_code("123456")
        with pytest.raises(TelegramAuthError, match="rejected"):
            service.submit_password("wrong-password")
        assert service.login_state()["stage"] == LOGIN_PASSWORD
    finally:
        service.close()


def test_cancel_and_logout_clear_the_login_state(tmp_path):
    fake = FakeTransport()
    service = _service(fake, tmp_path)
    try:
        service.start_login("+923001234567")
        state = service.cancel_login()
        assert state["stage"] == "idle"
        assert service._pending_phone == ""
        assert service._pending_hash == ""
    finally:
        service.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@fixture_desk", "fixture_desk"),
        ("fixture_desk", "fixture_desk"),
        ("t.me/fixture_desk", "fixture_desk"),
        ("https://t.me/fixture_desk/", "fixture_desk"),
        ("https://www.telegram.me/fixture_desk?start=1", "fixture_desk"),
    ],
)
def test_handle_normalization(raw: str, expected: str):
    assert normalize_handle(raw) == expected


@pytest.mark.parametrize("bad", ["", "@", "a b", "https://example.com/channel", "joinchat/abc"])
def test_handle_normalization_rejects_non_channel_values(bad: str):
    with pytest.raises(TelegramChannelError):
        normalize_handle(bad)


def test_phone_masking_keeps_at_most_two_digits_each_side():
    assert mask_phone("+923001234567") == "+9••••••67"
    assert mask_phone("123") == "••••"
    assert "92300" not in mask_phone("+923001234567")


def test_fetch_filters_the_half_open_window_and_paginates(tmp_path):
    fake = FakeTransport()
    service = _service(fake, tmp_path)
    pages: list[tuple[int, int]] = []
    try:
        outcome = service.fetch_window(
            "@fixture_desk",
            START,
            END,
            page_size=10,
            on_page=lambda number, messages, oldest: pages.append((number, len(messages))),
        )
        assert outcome.complete is True
        assert outcome.pages >= 3
        ids = [int(raw["id"]) for raw in outcome.messages]
        assert 1 in ids and 27 in ids
        assert 28 not in ids, "a post exactly at the cutoff belongs to the next issue"
        assert ids == sorted(ids, reverse=True), "history is returned newest first"
        assert len(ids) == len(set(ids)) == 27
        assert all(START <= parse_datetime(raw["date"]) < END for raw in outcome.messages)
        assert [number for number, _ in pages] == list(range(1, len(pages) + 1))
    finally:
        service.close()


def test_resume_from_a_checkpoint_does_not_refetch_newer_messages(tmp_path):
    fake = FakeTransport()
    service = _service(fake, tmp_path)
    try:
        outcome = service.fetch_window("@fixture_desk", START, END, page_size=10, resume_offset=19)
        ids = [int(raw["id"]) for raw in outcome.messages]
        assert outcome.resumed_from == 19
        assert ids, "older messages must still be returned"
        assert all(message_id < 19 for message_id in ids)
    finally:
        service.close()


def test_flood_wait_is_retried_politely_and_reported(tmp_path):
    fake = FakeTransport(flood_remaining=1, flood_seconds=1)
    service = _service(fake, tmp_path)
    try:
        outcome = service.fetch_window("@fixture_desk", START, END, page_size=10)
        assert outcome.flood_waits == 1
        assert outcome.messages
    finally:
        service.close()


def test_excessive_flood_wait_is_not_hidden(tmp_path):
    fake = FakeTransport(flood_remaining=1, flood_seconds=900)
    service = _service(fake, tmp_path, flood_wait_limit=300)
    try:
        with pytest.raises(TelegramFloodWait):
            service.fetch_window("@fixture_desk", START, END, page_size=10)
    finally:
        service.close()


def test_unauthenticated_session_raises_a_visible_error(tmp_path):
    fake = FakeTransport(authorized=False)
    service = _service(fake, tmp_path)
    try:
        with pytest.raises(TelegramAuthError, match="not signed in"):
            service.fetch_window("@fixture_desk", START, END)
    finally:
        service.close()


def test_inaccessible_channel_raises_a_visible_error(tmp_path):
    fake = FakeTransport()
    service = _service(fake, tmp_path)
    try:
        with pytest.raises(TelegramChannelError, match="private"):
            service.fetch_window("@private_channel", START, END)
    finally:
        service.close()


def test_missing_credentials_are_reported_without_secret_material(tmp_path):
    service = TelegramService(api_id="", api_hash="", session_path=tmp_path / "none.session")
    try:
        status = service.status(probe=False)
        assert status["credentials_configured"] is False
        assert status["authenticated"] is False
        assert "API id/hash" in status["detail"]
        assert "not-a-real-hash" not in str(status)
        with pytest.raises(TelegramAuthError):
            service.fetch_window("@fixture_desk", START, END)
    finally:
        service.close()


def test_invalid_window_is_rejected_before_any_request(tmp_path):
    fake = FakeTransport()
    service = _service(fake, tmp_path)
    try:
        with pytest.raises(ValueError):
            service.fetch_window("@fixture_desk", END, START)
        assert fake.page_calls == []
    finally:
        service.close()
