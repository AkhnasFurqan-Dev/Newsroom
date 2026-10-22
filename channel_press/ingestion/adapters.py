from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from ..timeutil import parse_datetime


class ImportFormatError(ValueError):
    pass


@dataclass
class NormalizedMessage:
    message_id: int
    posted_at: datetime
    text: str
    edited_at: datetime | None = None
    grouped_id: int | None = None
    reply_to_message_id: int | None = None
    forward_origin: str = ""
    deleted: bool = False
    segments: list[dict] | None = None
    segment_meta: dict | None = None
    media: list[dict] | None = None
    tags: list[str] | None = None
    translation_en: str | None = None
    translator: str = ""
    raw: dict | None = None


def _flatten_telegram_text(value: Any) -> str:
    """Telegram Desktop exports text as a string or a list of fragments."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "link":
                    parts.append(str(item.get("text") or item.get("href") or ""))
                else:
                    parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return str(value)


def _parse_date(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    try:
        parsed = parse_datetime(str(value)) if value else None
    except (TypeError, ValueError) as exc:
        raise ImportFormatError("Message is missing a valid date.") from exc
    if parsed is None:
        raise ImportFormatError("Message is missing a valid date.")
    return parsed


def normalize_messages(payload: dict, *, media_root: Path | None = None) -> list[NormalizedMessage]:
    """Normalize Channel Press owner-import format and Telegram Desktop JSON.

    Both formats are treated as untrusted data. Unknown fields are preserved in
    ``raw`` but never interpreted as instructions.
    """
    if not isinstance(payload, dict):
        raise ImportFormatError("Import must be a JSON object.")
    raw_messages = payload.get("messages")
    if raw_messages is None and isinstance(payload.get("chats"), list):
        # Telegram Desktop full export: messages live under chats[].messages
        raw_messages = []
        for chat in payload["chats"]:
            if chat.get("type") in {"public_channel", "private_channel", "channel"}:
                raw_messages.extend(chat.get("messages", []))
    if not isinstance(raw_messages, list):
        raise ImportFormatError("Import must contain a 'messages' array.")
    result: list[NormalizedMessage] = []
    for raw in raw_messages:
        if not isinstance(raw, dict):
            continue
        msg_id = raw.get("id", raw.get("message_id"))
        if msg_id is None:
            continue
        try:
            msg_id = int(msg_id)
        except (TypeError, ValueError):
            continue
        if raw.get("type") in {"service", "messageService"} and not raw.get("text"):
            # Telegram Desktop service events are retained but marked as such.
            pass
        text = _flatten_telegram_text(raw.get("text"))
        if not text and isinstance(raw.get("caption"), str):
            text = raw["caption"]
        media = list(raw.get("media") or [])
        # Telegram Desktop: photo/file keys.
        if not media:
            if raw.get("photo"):
                media.append({"kind": "photo", "file": raw.get("photo"), "caption": text})
            elif raw.get("file"):
                kind = "video" if str(raw.get("media_type", "")).startswith("video") else "document"
                media.append({"kind": kind, "file": raw.get("file"), "caption": text})
        forward = raw.get("forward_from") or raw.get("forwarded_from") or raw.get("forward_origin")
        if isinstance(forward, dict):
            forward = forward.get("name") or forward.get("title") or "forwarded"
        deleted = bool(raw.get("deleted") or raw.get("is_deleted") or raw.get("tombstone"))
        segments = raw.get("segments") if isinstance(raw.get("segments"), list) else None
        segment_meta = raw.get("segment_meta") if isinstance(raw.get("segment_meta"), dict) else None
        result.append(
            NormalizedMessage(
                message_id=msg_id,
                posted_at=_parse_date(raw.get("date") or raw.get("posted_at")),
                edited_at=parse_datetime(raw.get("edited_at") or raw.get("edit_date")) if (raw.get("edited_at") or raw.get("edit_date")) else None,
                text=text.strip(),
                grouped_id=_int_or_none(raw.get("grouped_id")),
                reply_to_message_id=_int_or_none(raw.get("reply_to_message_id") or raw.get("reply_to")),
                forward_origin=str(forward or ""),
                deleted=deleted,
                segments=segments,
                segment_meta=segment_meta,
                media=media,
                tags=raw.get("tags") if isinstance(raw.get("tags"), list) else None,
                translation_en=raw.get("translation_en") if isinstance(raw.get("translation_en"), str) else None,
                translator=str(raw.get("translator") or ""),
                raw=raw,
            )
        )
    result.sort(key=lambda message: (message.posted_at, message.message_id))
    return result


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def load_json(path: Path) -> dict:
    if path.stat().st_size > 40 * 1024 * 1024:
        raise ImportFormatError("Import exceeds the 40 MB safety limit.")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ImportFormatError(f"Invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ImportFormatError("Top-level JSON must be an object.")
    return data


@dataclass
class FixtureAdapter:
    """Deterministic, credential-free source adapter for tests and the demo."""

    path: Path

    def fetch(self, start: datetime | None = None, end: datetime | None = None) -> list[NormalizedMessage]:
        payload = load_json(self.path)
        messages = normalize_messages(payload)
        if start:
            messages = [m for m in messages if m.posted_at >= start]
        if end:
            messages = [m for m in messages if m.posted_at < end]
        return messages


class DirectImportAdapter:
    """Owner-supplied, rights-cleared JSON import (mandatory path)."""

    def __init__(self, payload: dict):
        self.payload = payload

    def fetch(self) -> list[NormalizedMessage]:
        return normalize_messages(self.payload)


class TelegramUserAdapter:
    """Isolated MTProto history adapter.

    The adapter is intentionally not exercised in fixture mode. It requires
    Telethon, owner credentials and a recorded rights declaration. It fetches
    only the configured channel, never arbitrary public channels.
    """

    def __init__(self, api_id: str, api_hash: str, session_path: str, channel_handle: str):
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_path = session_path
        self.channel_handle = channel_handle

    def available(self) -> tuple[bool, str]:
        try:
            import telethon  # noqa: F401
        except ImportError:
            return False, "Telethon is not installed. Install the optional 'telegram' extra."
        if not self.api_id or not self.api_hash:
            return False, "Telegram API ID/hash are not configured."
        if not self.channel_handle:
            return False, "No channel handle is configured."
        return True, "ready"

    def fetch(self, start: datetime, end: datetime, limit: int = 500) -> list[NormalizedMessage]:
        ok, reason = self.available()
        if not ok:
            raise RuntimeError(reason)
        from telethon.sync import TelegramClient  # type: ignore

        with TelegramClient(self.session_path, int(self.api_id), self.api_hash) as client:
            entity = client.get_entity(self.channel_handle)
            collected: list[dict] = []
            # iter_messages is polite and bounded; caller owns authorization.
            for message in client.iter_messages(entity, offset_date=end, limit=limit, reverse=False):
                if message.date < start:
                    break
                text = message.message or ""
                media = []
                if message.photo:
                    media.append({"kind": "photo", "file": "", "width": 0, "height": 0})
                collected.append(
                    {
                        "id": message.id,
                        "date": message.date.isoformat(),
                        "text": text,
                        "grouped_id": message.grouped_id,
                        "reply_to_message_id": message.reply_to_msg_id,
                        "edited_at": message.edit_date.isoformat() if message.edit_date else None,
                        "forward_from": (
                            getattr(message.forward, "chat", None).title
                            if getattr(message, "forward", None) and getattr(message.forward, "chat", None)
                            else None
                        ),
                        "media": media,
                    }
                )
        return normalize_messages({"messages": collected})


def owner_import_schema() -> dict:
    return {
        "schema": "channel_press.export.v1",
        "channel": {"title": "string", "handle": "@handle", "is_public": False},
        "messages": [
            {
                "id": 1,
                "date": "2026-10-20T06:00:00Z",
                "text": "directly authored post",
                "grouped_id": None,
                "reply_to_message_id": None,
                "forward_from": None,
                "media": [{"kind": "photo", "file": "relative-file.jpg", "caption": "..."}],
                "segments": [{"text": "...", "topic": "national", "title": "..."}],
            }
        ],
    }


_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+")
_TRAILING_PUNCTUATION = ".,;:!?\"'"
_CLOSING_BRACKETS = {")": "(", "]": "[", "}": "{"}


def extract_links(text: str) -> list[str]:
    """Extract URLs from prose without swallowing sentence punctuation.

    A post that ends ``see https://example.org/flood-relief.`` cites the URL
    without the full stop. Balanced brackets stay (Wikipedia-style paths), but
    an unmatched closing bracket belongs to the surrounding sentence.
    """
    links: list[str] = []
    for match in _URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(_TRAILING_PUNCTUATION)
        while url and url[-1] in _CLOSING_BRACKETS and url.count(url[-1]) > url.count(
            _CLOSING_BRACKETS[url[-1]]
        ):
            url = url[:-1].rstrip(_TRAILING_PUNCTUATION)
        if url and url not in links:
            links.append(url)
    return links
