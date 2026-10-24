"""Telegram transport abstractions.

The live transport uses Telethon's MTProto *user* client. It is deliberately
isolated behind a small protocol so the collection logic can be tested without
network access and without pretending that a fixture is a live connection.

Nothing in this module logs or returns the API hash, the API id, session
strings, login codes or 2FA passwords.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


class TelegramTransportError(RuntimeError):
    """A user-facing failure from the transport."""


class TelegramAuthError(TelegramTransportError):
    pass


class TelegramFloodWait(TelegramTransportError):
    def __init__(self, seconds: int):
        self.seconds = max(1, int(seconds))
        super().__init__(f"Telegram asked this client to wait {self.seconds} seconds.")


class TelegramChannelError(TelegramTransportError):
    pass


@dataclass
class PageResult:
    """One pagination page, newest first."""

    messages: list[dict] = field(default_factory=list)
    oldest_id: int | None = None


class TelegramTransport(Protocol):
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def is_authorized(self) -> bool: ...
    async def send_code(self, phone: str) -> str: ...
    async def sign_in(self, phone: str, code: str, phone_code_hash: str) -> None: ...
    async def sign_in_password(self, password: str) -> None: ...
    async def me(self) -> dict: ...
    async def resolve(self, handle: str) -> dict: ...
    async def page(
        self,
        entity: Any,
        *,
        limit: int,
        offset_id: int = 0,
        offset_date: datetime | None = None,
    ) -> PageResult: ...
    async def download_media(self, entity: Any, message_id: int, target: Path) -> str: ...


def normalize_handle(value: str) -> str:
    """Accept ``@name``, ``name``, ``t.me/name`` or a ``https://t.me/name`` URL."""
    text = (value or "").strip()
    text = re.sub(r"^https?://", "", text, flags=re.I)
    text = re.sub(r"^(www\.)?(t\.me|telegram\.me)/", "", text, flags=re.I)
    text = text.split("?", 1)[0].split("#", 1)[0].strip("/")
    if text.startswith("@"):
        text = text[1:]
    if not re.fullmatch(r"[A-Za-z0-9_]{3,64}", text or ""):
        raise TelegramChannelError(
            "Enter a public channel as @username or https://t.me/username."
        )
    return text


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _flatten_caption(message: Any) -> str:
    return str(getattr(message, "message", "") or "")


class TelethonTransport:
    """Thin async wrapper around ``telethon.TelegramClient``.

    The client is created on the dedicated gateway event loop; it must not be
    used from the web thread directly.
    """

    def __init__(self, api_id: str, api_hash: str, session_path: Path):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_path = session_path
        self._client: Any = None

    async def connect(self) -> None:
        if not self.api_id or not self.api_hash:
            raise TelegramTransportError("Telegram API id/hash are not configured.")
        try:
            from telethon import TelegramClient
        except ImportError as exc:  # pragma: no cover - environment specific
            raise TelegramTransportError(
                "Telethon is not installed. Run: pip install -e \".[telegram]\""
            ) from exc
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        if self._client is None:
            self._client = TelegramClient(
                str(self.session_path),
                int(self.api_id),
                self.api_hash,
                device_model="Channel Press Desktop",
                system_version="Windows",
                app_version="1.0",
                connection_retries=3,
                retry_delay=2,
                timeout=30,
            )
        try:
            if not self._client.is_connected():
                await self._client.connect()
        except Exception as exc:  # noqa: BLE001 - mapped to an operator message
            raise TelegramTransportError(f"Could not connect to Telegram ({type(exc).__name__}).") from exc
        # Best-effort hardening of the session file (contains an auth key).
        try:
            if self.session_path.exists():
                self.session_path.chmod(0o600)
        except OSError:
            pass

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client.session.close()
                self._client = None

    async def is_authorized(self) -> bool:
        if self._client is None:
            return False
        try:
            return bool(await self._client.is_user_authorized())
        except Exception:  # noqa: BLE001
            return False

    async def send_code(self, phone: str) -> str:
        from telethon.errors import FloodWaitError, PhoneNumberInvalidError

        try:
            sent = await self._client.send_code_request(phone)
            return sent.phone_code_hash
        except FloodWaitError as exc:
            raise TelegramFloodWait(exc.seconds) from exc
        except PhoneNumberInvalidError as exc:
            raise TelegramAuthError("That phone number is not accepted by Telegram.") from exc
        except Exception as exc:  # noqa: BLE001
            raise TelegramAuthError(f"Telegram refused the login code request ({type(exc).__name__}).") from exc

    async def sign_in(self, phone: str, code: str, phone_code_hash: str) -> None:
        from telethon.errors import (
            FloodWaitError,
            PhoneCodeExpiredError,
            PhoneCodeInvalidError,
            SessionPasswordNeededError,
        )

        try:
            await self._client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError as exc:
            raise TelegramAuthError("2FA password required.") from exc
        except PhoneCodeInvalidError as exc:
            raise TelegramAuthError("The login code was rejected. Check the code and retry.") from exc
        except PhoneCodeExpiredError as exc:
            raise TelegramAuthError("The login code expired. Request a new code.") from exc
        except FloodWaitError as exc:
            raise TelegramFloodWait(exc.seconds) from exc
        except Exception as exc:  # noqa: BLE001
            raise TelegramAuthError(f"Sign-in failed ({type(exc).__name__}).") from exc

    async def sign_in_password(self, password: str) -> None:
        from telethon.errors import FloodWaitError, PasswordHashInvalidError

        try:
            await self._client.sign_in(password=password)
        except PasswordHashInvalidError as exc:
            raise TelegramAuthError("The 2FA password was rejected.") from exc
        except FloodWaitError as exc:
            raise TelegramFloodWait(exc.seconds) from exc
        except Exception as exc:  # noqa: BLE001
            raise TelegramAuthError(f"2FA sign-in failed ({type(exc).__name__}).") from exc

    async def me(self) -> dict:
        try:
            user = await self._client.get_me()
        except Exception as exc:  # noqa: BLE001
            raise TelegramTransportError(f"Could not read the account identity ({type(exc).__name__}).") from exc
        if user is None:
            raise TelegramAuthError("The saved session is no longer signed in.")
        return {
            "id": int(user.id),
            "first_name": user.first_name or "",
            "last_name": user.last_name or "",
            "username": user.username or "",
        }

    async def resolve(self, handle: str) -> dict:
        from telethon.errors import ChannelPrivateError, UsernameInvalidError, UsernameNotOccupiedError

        username = normalize_handle(handle)
        try:
            entity = await self._client.get_entity(username)
        except (UsernameNotOccupiedError, UsernameInvalidError, ValueError) as exc:
            raise TelegramChannelError(f"@{username} was not found or is not a public channel.") from exc
        except ChannelPrivateError as exc:
            raise TelegramChannelError(
                f"@{username} is private or this account cannot read it. Ask the channel owner for access."
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise TelegramChannelError(f"Channel resolution failed ({type(exc).__name__}).") from exc
        return {
            "id": int(entity.id),
            "title": getattr(entity, "title", "") or username,
            "username": getattr(entity, "username", "") or username,
            "is_channel": bool(getattr(entity, "broadcast", False)),
            "is_megagroup": bool(getattr(entity, "megagroup", False)),
        }

    async def page(
        self,
        entity: Any,
        *,
        limit: int,
        offset_id: int = 0,
        offset_date: datetime | None = None,
    ) -> PageResult:
        from telethon.errors import FloodWaitError

        try:
            batch = await self._client.get_messages(
                entity,
                limit=limit,
                offset_id=offset_id,
                offset_date=offset_date,
            )
        except FloodWaitError as exc:
            raise TelegramFloodWait(exc.seconds) from exc
        except Exception as exc:  # noqa: BLE001
            raise TelegramTransportError(f"History request failed ({type(exc).__name__}).") from exc
        messages = [message_to_dict(message) for message in batch]
        return PageResult(
            messages=messages,
            oldest_id=messages[-1]["id"] if messages else None,
        )

    async def download_media(self, entity: Any, message_id: int, target: Path) -> str:
        """Download one message's media to ``target`` (lazy, opt-in)."""
        target.parent.mkdir(parents=True, exist_ok=True)
        message = await self._client.get_messages(entity, ids=message_id)
        if message is None or not message.media:
            raise TelegramTransportError("That message has no downloadable media.")
        path = await self._client.download_media(message, file=str(target))
        if not path:
            raise TelegramTransportError("Telegram did not return media data.")
        return str(path)


def message_to_dict(message: Any) -> dict:
    """Convert a Telethon message into the untrusted source payload shape.

    Text stays clean prose; entity hyperlinks and the web-page preview URL are
    preserved separately so citations can be resolved without rewriting the
    author's words.
    """
    from telethon import utils as telethon_utils

    text = _flatten_caption(message)
    links: list[str] = []
    try:
        entities = list(getattr(message, "entities", None) or [])
    except Exception:  # noqa: BLE001 - entity parsing must never break ingestion
        entities = []
    for entity in entities:
        url = getattr(entity, "url", None)
        if url:
            links.append(str(url))
    webpage = getattr(getattr(message, "media", None), "webpage", None)
    if webpage is not None:
        for attr in ("url", "display_url"):
            value = getattr(webpage, attr, None)
            if value:
                links.append(str(value))
    forward = getattr(message, "forward", None)
    forward_from = ""
    if forward is not None:
        chat = getattr(forward, "chat", None)
        sender = getattr(forward, "sender", None)
        forward_from = (
            getattr(chat, "title", None)
            or getattr(sender, "username", None)
            or getattr(sender, "first_name", None)
            or getattr(forward, "from_name", None)
            or "forwarded"
        )
        forward_from = str(forward_from)
    media: list[dict] = []
    photo = getattr(message, "photo", None)
    document = getattr(message, "document", None)
    if photo is not None:
        media.append(
            {
                "kind": "photo",
                "telegram_file_id": str(getattr(photo, "id", "")),
                "width": int(getattr(photo, "width", 0) or 0),
                "height": int(getattr(photo, "height", 0) or 0),
                "caption": text,
            }
        )
    elif document is not None:
        attributes = getattr(document, "attributes", None) or []
        duration = 0.0
        is_video = bool(getattr(document, "mime_type", "").startswith("video"))
        for attribute in attributes:
            if hasattr(attribute, "duration"):
                duration = float(attribute.duration or 0)
        media.append(
            {
                "kind": "video" if is_video else "document",
                "telegram_file_id": str(getattr(document, "id", "")),
                "duration": duration,
                "bytes": int(getattr(document, "size", 0) or 0),
                "mime_type": str(getattr(document, "mime_type", "") or ""),
                "caption": text,
            }
        )
    payload: dict = {
        "id": int(message.id),
        "date": _utc(message.date).isoformat(),
        "text": text,
        "edited_at": _utc(getattr(message, "edit_date", None)).isoformat()
        if getattr(message, "edit_date", None)
        else None,
        "grouped_id": getattr(message, "grouped_id", None),
        "reply_to_message_id": getattr(message, "reply_to_msg_id", None),
        "forward_from": forward_from or None,
        "media": media,
        "entity_links": links,
        "has_media": bool(photo is not None or document is not None),
        "via_bot": str(getattr(getattr(message, "via_bot", None), "username", "") or ""),
        "views": getattr(message, "views", None),
    }
    # ``sender`` on a channel post is the channel itself; keep a stable marker
    # so forwarded/native reposts can be excluded by the normalization rules.
    try:
        peer = telethon_utils.get_peer_id(message.peer_id) if message.peer_id else None
        payload["peer_id"] = int(peer) if peer is not None else None
    except Exception:  # noqa: BLE001
        payload["peer_id"] = None
    return payload


async def polite_pause(seconds: float = 0.4) -> None:
    await asyncio.sleep(seconds)
