"""Authorized Telegram user-session gateway.

Design constraints:

* One dedicated event-loop thread owns the Telethon client, so the synchronous
  FastAPI views and the CLI share a single connection.
* Login codes and 2FA passwords are accepted only through the local UI/console,
  held in memory for the duration of the login and never written to disk.
* The session file is created by Telethon in the ignored data directory.
* History retrieval is paginated, polite and resumable. A page callback lets
  the caller persist a checkpoint before the next page is requested.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

from ..config import settings
from ..timeutil import parse_datetime, utcnow
from .transport import (
    PageResult,
    TelegramAuthError,
    TelegramChannelError,
    TelegramFloodWait,
    TelegramTransport,
    TelegramTransportError,
    TelethonTransport,
    normalize_handle,
)

LOGIN_IDLE = "idle"
LOGIN_PHONE = "awaiting_phone"
LOGIN_CODE = "awaiting_code"
LOGIN_PASSWORD = "awaiting_password"
LOGIN_AUTHORIZED = "authorized"
LOGIN_ERROR = "error"

PHONE_RE = re.compile(r"^\+?[0-9][0-9\s\-()]{5,20}$")


@dataclass
class LoginState:
    stage: str = LOGIN_IDLE
    phone_masked: str = ""
    message: str = ""
    started_at: datetime | None = None
    expires_at: datetime | None = None

    def expired(self) -> bool:
        return bool(self.expires_at and utcnow() >= self.expires_at)

    def public(self) -> dict:
        data = asdict(self)
        data["expires_in"] = (
            max(0, int((self.expires_at - utcnow()).total_seconds())) if self.expires_at else 0
        )
        data["expired"] = self.expired()
        return data


@dataclass
class FetchOutcome:
    channel: dict
    messages: list[dict]
    pages: int = 0
    resumed_from: int = 0
    complete: bool = True
    flood_waits: int = 0
    duration_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)


class TransportFactory(Protocol):
    def __call__(self) -> TelegramTransport: ...


def mask_phone(phone: str) -> str:
    digits = re.sub(r"[^0-9+]", "", phone)
    if len(digits) <= 4:
        return "••••"
    return f"{digits[:2]}••••••{digits[-2:]}"


class TelegramService:
    """Process-wide gateway. Construct once; share through ``telegram_service``."""

    def __init__(
        self,
        *,
        api_id: str | None = None,
        api_hash: str | None = None,
        session_path: Path | None = None,
        factory: TransportFactory | None = None,
        flood_wait_limit: int = 300,
    ):
        self.api_id = api_id if api_id is not None else settings.telegram_api_id
        self.api_hash = api_hash if api_hash is not None else settings.telegram_api_hash
        self.session_path = session_path or settings.telegram_session_file
        self.flood_wait_limit = flood_wait_limit
        self._factory = factory
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._transport: TelegramTransport | None = None
        self._lock: asyncio.Lock | None = None
        self._login = LoginState()
        self._status_cache: dict | None = None
        self._status_at = 0.0
        self._connected = False
        self._pending_phone = ""
        self._pending_hash = ""

    # -- infrastructure ----------------------------------------------------

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            loop = asyncio.new_event_loop()
            thread = threading.Thread(target=loop.run_forever, name="telegram-gateway", daemon=True)
            thread.start()
            self._loop = loop
            self._thread = thread
        return self._loop

    def _submit(self, coro: Any, *, timeout: float = 90.0) -> Any:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TelegramTransportError("The Telegram request timed out.") from exc

    def _new_transport(self) -> TelegramTransport:
        if self._factory is not None:
            return self._factory()
        return TelethonTransport(self.api_id, self.api_hash, self.session_path)

    def credentials_configured(self) -> bool:
        return bool(str(self.api_id).strip() and str(self.api_hash).strip())

    def session_present(self) -> bool:
        try:
            return self.session_path.exists() and self.session_path.stat().st_size > 0
        except OSError:
            return False

    def close(self) -> None:
        if self._loop is None:
            return
        try:
            self._submit(self._shutdown(), timeout=10)
        except Exception:  # noqa: BLE001
            pass

    async def _shutdown(self) -> None:
        if self._transport is not None:
            await self._transport.disconnect()
            self._transport = None
        self._connected = False
        self._login = LoginState()

    # -- connection --------------------------------------------------------

    async def _connect(self) -> TelegramTransport:
        if not self.credentials_configured():
            raise TelegramAuthError(
                "Telegram API id/hash are missing. Edit .env locally and reload the app."
            )
        if self._transport is None:
            self._transport = self._new_transport()
        if not self._connected:
            await self._transport.connect()
            self._connected = True
        return self._transport

    async def _authorized_transport(self) -> TelegramTransport:
        transport = await self._connect()
        if not await transport.is_authorized():
            raise TelegramAuthError(
                "This Telegram session is not signed in. Complete the local login first."
            )
        return transport

    async def _status(self, probe: bool) -> dict:
        state = {
            "credentials_configured": self.credentials_configured(),
            "session_present": self.session_present(),
            "authenticated": False,
            "account": None,
            "detail": "",
            "checked_at": utcnow().isoformat(),
        }
        if not state["credentials_configured"]:
            state["detail"] = "API id/hash are not configured in .env."
            return state
        if not probe and not state["session_present"]:
            state["detail"] = "No local session yet. Start the authorized login."
            return state
        try:
            transport = await self._connect()
            authorized = await transport.is_authorized()
            state["authenticated"] = authorized
            if authorized:
                me = await transport.me()
                state["account"] = {
                    "username": me.get("username", ""),
                    "display_name": " ".join(
                        part for part in (me.get("first_name"), me.get("last_name")) if part
                    ),
                    "id": me.get("id"),
                }
                state["detail"] = "Signed in."
            else:
                state["detail"] = "Session present but not signed in. Complete the login."
        except TelegramTransportError as exc:
            state["detail"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - never leak a raw traceback to the UI
            state["detail"] = f"Telegram check failed ({type(exc).__name__})."
        return state

    def status(self, *, probe: bool = False) -> dict:
        now = time.monotonic()
        if not probe and self._status_cache and now - self._status_at < 60:
            cached = dict(self._status_cache)
            cached["cached"] = True
            return cached
        result = self._submit(self._status(probe=probe), timeout=45)
        result["cached"] = False
        self._status_cache = result
        self._status_at = now
        return result

    # -- login -------------------------------------------------------------

    def login_state(self) -> dict:
        if self._login.expired() and self._login.stage in {LOGIN_CODE, LOGIN_PASSWORD, LOGIN_PHONE}:
            self._login = LoginState(stage=LOGIN_ERROR, message="Login window expired. Start again.")
        return self._login.public()

    def _login_timeout(self) -> timedelta:
        return timedelta(seconds=max(60, int(settings.telegram_login_timeout_seconds)))

    def start_login(self, phone: str) -> dict:
        phone = (phone or "").strip()
        if not PHONE_RE.match(phone):
            raise TelegramAuthError("Enter the phone number in international format, e.g. +92300…")
        if self._login.stage in {LOGIN_CODE, LOGIN_PASSWORD} and not self._login.expired():
            raise TelegramAuthError("A login is already in progress. Cancel it before starting again.")
        result = self._submit(self._start_login(phone), timeout=60)
        return result

    async def _start_login(self, phone: str) -> dict:
        transport = await self._connect()
        code_hash = await transport.send_code(phone)
        self._login = LoginState(
            stage=LOGIN_CODE,
            phone_masked=mask_phone(phone),
            message="Telegram sent a login code to the account.",
            started_at=utcnow(),
            expires_at=utcnow() + self._login_timeout(),
        )
        # The raw phone and code hash stay in memory only.
        self._pending_phone = phone
        self._pending_hash = code_hash
        return self._login.public()

    def submit_code(self, code: str) -> dict:
        code = (code or "").strip().replace(" ", "")
        if self._login.stage != LOGIN_CODE:
            raise TelegramAuthError("No login code is expected right now.")
        if self._login.expired():
            raise TelegramAuthError("The login window expired. Start again.")
        if not re.fullmatch(r"[0-9]{4,8}", code):
            raise TelegramAuthError("Enter the numeric login code.")
        return self._submit(self._submit_code(code), timeout=90)

    async def _submit_code(self, code: str) -> dict:
        transport = await self._connect()
        phone = getattr(self, "_pending_phone", "")
        phone_hash = getattr(self, "_pending_hash", "")
        try:
            await transport.sign_in(phone, code, phone_hash)
        except TelegramAuthError as exc:
            if "2FA" in str(exc):
                self._login = LoginState(
                    stage=LOGIN_PASSWORD,
                    phone_masked=self._login.phone_masked,
                    message="Two-step verification is enabled. Enter the account password.",
                    started_at=self._login.started_at,
                    expires_at=self._login.expires_at,
                )
                return self._login.public()
            raise
        # Nothing secret survives a successful sign-in.
        self._pending_phone = ""
        self._pending_hash = ""
        self._login = LoginState(stage=LOGIN_AUTHORIZED, message="Signed in.")
        self._status_cache = None
        return self._login.public()

    def submit_password(self, password: str) -> dict:
        if self._login.stage != LOGIN_PASSWORD:
            raise TelegramAuthError("No 2FA password is expected right now.")
        if self._login.expired():
            raise TelegramAuthError("The login window expired. Start again.")
        if not password:
            raise TelegramAuthError("Enter the two-step verification password.")
        return self._submit(self._submit_password(password), timeout=90)

    async def _submit_password(self, password: str) -> dict:
        transport = await self._connect()
        await transport.sign_in_password(password)
        self._pending_phone = ""
        self._pending_hash = ""
        self._login = LoginState(stage=LOGIN_AUTHORIZED, message="Signed in.")
        self._status_cache = None
        return self._login.public()

    def cancel_login(self) -> dict:
        self._pending_phone = ""
        self._pending_hash = ""
        self._login = LoginState(stage=LOGIN_IDLE, message="Login cancelled.")
        return self._login.public()

    def logout(self) -> dict:
        """Explicitly terminate the Telegram session. Destructive; UI confirms."""
        self._submit(self._logout(), timeout=30)
        self._login = LoginState(stage=LOGIN_IDLE, message="Signed out and the local session was removed.")
        self._status_cache = None
        return self.login_state()

    async def _logout(self) -> None:
        transport = await self._connect()
        client = getattr(transport, "_client", None)
        if client is not None:
            await client.log_out()
        await transport.disconnect()
        self._transport = None
        self._connected = False

    # -- channel and history ----------------------------------------------

    def resolve_channel(self, handle: str) -> dict:
        handle = normalize_handle(handle)
        return self._submit(self._resolve(handle), timeout=60)

    async def _resolve(self, handle: str) -> dict:
        transport = await self._authorized_transport()
        return await transport.resolve(handle)

    def fetch_window(
        self,
        handle: str,
        start: datetime,
        end: datetime,
        *,
        limit: int | None = None,
        page_size: int = 100,
        resume_offset: int = 0,
        on_page: Callable[[int, list[dict], int | None], None] | None = None,
    ) -> FetchOutcome:
        if start >= end:
            raise ValueError("Window start must be before window end.")
        limit = limit or settings.telegram_history_limit
        page_size = max(10, min(page_size, 200))
        return self._submit(
            self._fetch(handle, start, end, limit, page_size, resume_offset, on_page),
            timeout=max(120.0, settings.telegram_history_limit / 10),
        )

    async def _fetch(
        self,
        handle: str,
        start: datetime,
        end: datetime,
        limit: int,
        page_size: int,
        resume_offset: int,
        on_page: Callable[[int, list[dict], int | None], None] | None,
    ) -> FetchOutcome:
        began = time.monotonic()
        transport = await self._authorized_transport()
        username = normalize_handle(handle)
        channel = await transport.resolve(username)
        messages: list[dict] = []
        offset_id = int(resume_offset or 0)
        offset_date = end if offset_id == 0 else None
        pages = 0
        flood_waits = 0
        complete = True
        while len(messages) < limit:
            remaining = min(page_size, limit - len(messages))
            page: PageResult | None = None
            for attempt in range(3):
                try:
                    page = await transport.page(
                        username,
                        limit=remaining,
                        offset_id=offset_id,
                        offset_date=offset_date,
                    )
                    break
                except TelegramFloodWait as exc:
                    flood_waits += 1
                    if exc.seconds > self.flood_wait_limit:
                        raise
                    await asyncio.sleep(exc.seconds + 1)
                except (TelegramAuthError, TelegramChannelError):
                    raise
                except TelegramTransportError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 ** attempt)
            if page is None or not page.messages:
                break
            pages += 1
            reached_start = False
            for raw in page.messages:
                posted = parse_datetime(raw.get("date"))
                if posted is None:
                    continue
                if posted >= end:
                    continue
                if posted < start:
                    reached_start = True
                    continue
                messages.append(raw)
            if on_page is not None:
                on_page(pages, page.messages, page.oldest_id)
            if page.oldest_id is None or page.oldest_id == offset_id:
                break
            offset_id = page.oldest_id
            offset_date = None
            if reached_start or len(page.messages) < remaining:
                break
            await asyncio.sleep(0.35)
        else:
            complete = False
        if len(messages) >= limit:
            complete = False
        return FetchOutcome(
            channel=channel,
            messages=messages,
            pages=pages,
            resumed_from=int(resume_offset or 0),
            complete=complete,
            flood_waits=flood_waits,
            duration_seconds=round(time.monotonic() - began, 2),
            notes=[] if complete else ["History limit reached; older messages were not requested."],
        )

    def download_media(self, handle: str, message_id: int, target: Path) -> str:
        return self._submit(self._download(handle, message_id, target), timeout=180)

    async def _download(self, handle: str, message_id: int, target: Path) -> str:
        transport = await self._authorized_transport()
        return await transport.download_media(normalize_handle(handle), message_id, target)


telegram_service = TelegramService()


def reset_service_for_tests(service: TelegramService | None = None) -> TelegramService:
    """Replace the module-level service (tests only)."""
    global telegram_service
    telegram_service = service or TelegramService()
    return telegram_service
