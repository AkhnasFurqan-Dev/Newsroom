"""Deterministic cleaning of source-derived text for reader-facing output.

Telegram posts often begin with decorative bulletin furniture — flag emojis,
lightning bolts, ``BREAKING`` labels, ``@channel REPORTED`` stamps, chains of
slashes. That material is evidence of how a channel broadcasts, not part of a
news story, and it must not reach a printed newspaper or a reader transcript.

This module never touches the stored source text. Callers apply it only to the
copy they are about to publish, so the original message, its message ID and its
timestamps remain exactly as ingested for audit and rights purposes.

Unicode that carries meaning — Arabic/Urdu script, accented Latin names,
typographic quotes, en dashes inside ranges — is preserved.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Emoji, pictographs, flags, dingbats and the variation selectors that trail
# them. Kept as one pattern so a run of decorations collapses to one space.
_DECOR = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\u2B00-\u2BFF"
    "\uFE0F\uFE0E"
    "\u200B-\u200D\uFEFF"
    "\u2022\u25AA\u25AB\u25CF\u25CB\u25A0\u25A1"
    "]+"
)
# A channel handle is broadcast furniture in body copy. Genuine attribution is
# written out ("the channel said"), never as a bare @handle.
_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_]{3,}")
# Status stamps. Matched only as a standalone label (optionally followed by a
# colon or dash) or when the original was upper-case, so ordinary prose such as
# "the government update was delayed" is not mangled.
_STATUS_LABEL = re.compile(
    r"(?i)(?:^|(?<=[.!?]\s))"
    r"(?:breaking(?:\s+news)?|news\s+alert|alert|update[sd]?|updated|urgent|"
    r"exclusive|just\s+in|developing|reported|via|watch|must\s+read|"
    r"important\s+notice|announcement)"
    r"\s*[:|\-\u2013\u2014]\s*"
)
_STATUS_UPPER = re.compile(
    r"\b(?:REPORTED|BREAKING(?:\s+NEWS)?|URGENT|EXCLUSIVE|JUST\s+IN|"
    r"DEVELOPING|UPDATE|UPDATED|VIA)\b\s*[:|\-\u2013\u2014]?"
)
# Repeated separators such as "//", "||", ">>", "——" used as bulletin dividers.
_SEPARATOR_RUN = re.compile(r"\s*(?:[/|>]{2,}|[\u2013\u2014]{2,}|\*{2,})\s*")
_MULTISPACE = re.compile(r"[ \t\u00a0]+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%])")
_REPEATED_PUNCT = re.compile(r"([!?])\1{1,}")


def strip_output_decor(text: str) -> str:
    """Return a clean, printable form of a source-derived string.

    The transformation is deliberately conservative: it removes broadcast
    furniture and normalises spacing, never rewrites wording or drops a
    factual clause.
    """
    value = unicodedata.normalize("NFKC", text or "")
    value = _DECOR.sub(" ", value)
    value = _STATUS_UPPER.sub(" ", value)
    value = _HANDLE.sub(" ", value)
    value = _SEPARATOR_RUN.sub(" ", value)
    # Leading status labels ("BREAKING: ...") are handled above when upper-case;
    # title-case or lower-case labels are removed here, once, at the start.
    value = _STATUS_LABEL.sub("", value, count=1)
    value = _REPEATED_PUNCT.sub(r"\1", value)
    value = _MULTISPACE.sub(" ", value)
    value = _SPACE_BEFORE_PUNCT.sub(r"\1", value)
    value = re.sub(r"\s+([)\]])", r"\1", value)
    value = re.sub(r"([(\[])\s+", r"\1", value)
    return value.strip(" \t-|\u2013\u2014")


def clean_sentence(text: str) -> str:
    """Clean one sentence and guarantee terminal punctuation."""
    value = strip_output_decor(text)
    if value and value[-1] not in ".!?\u2026\"'\u201d":
        value += "."
    return value


def is_mostly_decorative(text: str) -> bool:
    """True when removing emoji/handles leaves too little text to report."""
    cleaned = strip_output_decor(text)
    return len(re.findall(r"[A-Za-z\u0600-\u06ff]", cleaned)) < 12


def similarity(left: str, right: str) -> float:
    """Similarity of two normalised strings in the range 0..1."""
    a = re.sub(r"[^a-z0-9\u0600-\u06ff ]+", " ", (left or "").lower())
    b = re.sub(r"[^a-z0-9\u0600-\u06ff ]+", " ", (right or "").lower())
    a = " ".join(a.split())
    b = " ".join(b.split())
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def is_near_duplicate(left: str, right: str, threshold: float = 0.82) -> bool:
    """True when two source updates say materially the same thing."""
    if similarity(left, right) >= threshold:
        return True
    a = set(re.findall(r"[a-z0-9\u0600-\u06ff]+", (left or "").lower()))
    b = set(re.findall(r"[a-z0-9\u0600-\u06ff]+", (right or "").lower()))
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= 0.7
