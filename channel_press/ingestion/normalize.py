from __future__ import annotations

import re

from ..ingestion.adapters import NormalizedMessage

PROMO_PATTERNS = [
    r"(?i)^\s*join (our|the) (new )?channel",
    r"(?i)^\s*follow (us|our channel)",
    r"(?i)^\s*subscribe to",
    r"(?i)^\s*https?://t\.me/[a-z0-9_]+\s*$",
    r"(?i)exclusive videos.*https?://t\.me/",
]


def detect_language(text: str) -> str:
    if not text:
        return "en"
    arabic = sum(1 for ch in text if "\u0600" <= ch <= "\u06ff")
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    if arabic > latin * 0.5:
        return "ur"
    return "en"


def promotion_only(message: NormalizedMessage) -> bool:
    text = message.text.strip()
    if not text:
        return False
    if message.raw and message.raw.get("promotion") is True:
        return True
    if any(re.search(pattern, text) for pattern in PROMO_PATTERNS):
        return True
    # A bare channel link plus a call to action.
    words = re.findall(r"[A-Za-z]{2,}", re.sub(r"https?://\S+", "", text))
    return len(words) < 8 and "t.me/" in text and any(
        word in text.lower() for word in ("join", "follow", "subscribe", "channel")
    )


def exclusion_reason(message: NormalizedMessage, *, rights_ok: bool) -> str | None:
    if message.deleted:
        return "deleted"
    if message.forward_origin:
        return "telegram_forward"
    if not message.text.strip() and not (message.media or []):
        return "empty"
    if message.raw and message.raw.get("type") in {"service", "messageService"} and not message.text.strip():
        return "service_event"
    if promotion_only(message):
        return "promotion_only"
    if not rights_ok:
        return "rights_unconfirmed"
    return None


def split_segments(message: NormalizedMessage) -> list[dict]:
    """Return attributable fragments without inventing content.

    Explicit fixture/provider segmentation wins. Otherwise a blank-line split
    is used only when the post clearly carries two unrelated labelled topics.
    """
    if message.segments:
        fragments = []
        for sequence, segment in enumerate(message.segments):
            if not isinstance(segment, dict):
                continue
            text = str(segment.get("text") or "").strip()
            if not text:
                continue
            start = message.text.find(text)
            fragments.append(
                {
                    "text": text,
                    "start": max(start, 0),
                    "end": max(start, 0) + len(text) if start >= 0 else len(text),
                    "topics": [segment.get("topic")] if segment.get("topic") else (message.tags or []),
                    "title": str(segment.get("title") or "").strip(),
                    "incident_key": str(segment.get("incident_key") or "").strip(),
                    "location": str(segment.get("location") or "").strip(),
                    "genre": str(segment.get("genre") or (message.segment_meta or {}).get("genre") or "news"),
                    "sequence": sequence,
                    "article_seed": segment.get("article_seed") if isinstance(segment.get("article_seed"), dict) else None,
                }
            )
        if fragments:
            return fragments
    meta = message.segment_meta or {}
    text = message.text.strip()
    if not text:
        return []
    seed = meta.get("article_seed")
    if seed is None and message.raw:
        candidate = message.raw.get("article_seed")
        if isinstance(candidate, dict):
            seed = candidate
    return [
        {
            "text": text,
            "start": 0,
            "end": len(message.text),
            "topics": message.tags or ([meta.get("topic")] if meta.get("topic") else []),
            "title": str(meta.get("title") or "").strip(),
            "incident_key": str(meta.get("incident_key") or "").strip(),
            "location": str(meta.get("location") or "").strip(),
            "genre": str(meta.get("genre") or "news"),
            "sequence": 0,
            "article_seed": seed if isinstance(seed, dict) else None,
        }
    ]


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
