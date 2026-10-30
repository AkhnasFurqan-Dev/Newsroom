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
# A bulletin often opens with a mixed run of slashes, tildes, arrows and dashes
# ("/ ~ ", "— ", ">> "). Strip only the leading decoration, never a hyphenated
# word or a number.
_LEADING_DECOR = re.compile(r"^[\s/\\~|>*\u2022\u00b7\u2013\u2014-]+")
_MULTISPACE = re.compile(r"[ \t\u00a0]+")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%])")
_REPEATED_PUNCT = re.compile(r"([!?])\1{1,}")
_EMPTY_PARENS = re.compile(r"\(\s*\)")
_DANGLING_COMMA = re.compile(r",\s*([.!?])")
_REPEATED_COMMA = re.compile(r",\s*,")
_COMMON_TYPOS = {
    "policys": "policies",
    "countrys": "countries",
    "citys": "cities",
    "authorites": "authorities",
    "goverment": "government",
    "seperate": "separate",
    "occured": "occurred",
}

# Model output sometimes glues the source relationship into ungrammatical
# phrases. These are presentation repairs only: the underlying claim and its
# attribution remain in the evidence record.
_DOUBLE_ATTRIBUTION = re.compile(
    r"(?i)\b(reported|said|noted|stated|added|confirmed|claimed)\s+"
    r"(reported|said|noted|stated|added|confirmed|claimed)\b"
)
_REDUNDANT_SOURCE_CLAUSE = re.compile(
    r"(?i),\s*(?:according\s+to|as\s+per)\s+"
    r"(?:a|an|the)?\s*(?:recent\s+|official\s+|earlier\s+)?"
    r"(?:message|report|statement|post|update|note)"
    r"(?:\s+(?:from|by|on)\s+(?:the\s+)?(?:source\s+)?(?:channel|news\s+channel|publication))?"
    r"(?:\s+(?:reported|said|noted|stated|added|confirmed))?"
)
_CHANNEL_REPORTING = re.compile(
    r"(?i)\bwith\s+(?:the\s+)?(?:source\s+)?channel\s+reporting\s+that\b"
)
_CHANNEL_ATTRIBUTION = re.compile(
    r"(?i)\b(?:the|this)\s+(?:source\s+)?(?:news\s+)?channel\s+"
    r"(?:reported|said|noted|stated|added|confirmed)\b"
)
# Padding clauses that repeat an attribution already made in the lead.
_REDUNDANT_TRAILING_ATTRIBUTION = re.compile(
    r"(?i),\s*(?:as\s+(?:reported|detailed|noted)\b"
    r"(?:\s+(?:in|by)\s+(?:the\s+)?(?:message|post|source|channel|report))?"
    r"|as\s+detailed\s+in\s+the\s+message)\s*(?=[.;]|$)"
)

# Function words are not "news": a paragraph that adds only more function words
# is restating what the paper already printed.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "of", "in", "on", "at", "to", "for", "from", "by", "with",
    "as", "is", "are", "was", "were", "be", "been", "being", "has", "have",
    "had", "do", "does", "did", "will", "would", "could", "should", "may",
    "might", "can", "it", "its", "he", "she", "they", "them", "their", "his",
    "her", "we", "our", "you", "your", "i", "my", "me", "us", "who", "whom",
    "which", "what", "when", "where", "why", "how", "also", "more", "most",
    "very", "said", "says", "according", "reported", "noted", "stated",
    "added", "claimed", "about", "into", "over", "after", "before", "while",
    "during", "between", "against", "under", "up", "out", "so", "such",
}
_NUMBER = re.compile(r"\b\d[\d,.]*\b")


def _stem(word: str) -> str:
    """Very small suffix stripper, enough to match plural/participle variants."""
    for suffix in ("ations", "ation", "ings", "ing", "ies", "es", "ed", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            base = word[: -len(suffix)]
            return base + "y" if suffix == "ies" else base
    return word


def _normalise_word(word: str) -> str:
    value = word.lower().replace("\u2019", "'")
    if value.endswith("'s"):
        value = value[:-2]
    return value.strip("'")


def _content_words(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z0-9'\u2019-]+", text or "")
    return [
        _stem(_normalise_word(word))
        for word in words
        if _normalise_word(word) not in _STOPWORDS
    ]


def _fresh_facts(candidate: str, prior: str) -> int:
    """Count numbers and capitalised names in *candidate* absent from *prior*."""
    prior_lower = (prior or "").lower()
    fresh = 0
    for number in _NUMBER.findall(candidate or ""):
        if number.lower() not in prior_lower:
            fresh += 1
    tokens = re.findall(r"\b[A-Z][A-Za-z0-9'\u2019-]{2,}\b", candidate or "")
    for token in tokens:
        if _normalise_word(token) not in prior_lower:
            fresh += 1
    return fresh


def has_verbatim_run(candidate: str, prior: str, *, size: int = 7) -> bool:
    """True when *candidate* repeats a long word-for-word run from *prior*.

    Paraphrased padding is hard to judge deterministically, but a shared run of
    seven or more words is copied material, not a new paragraph. The check is
    word-based so punctuation and quote style do not hide the repetition.
    """
    cand_words = [word.lower() for word in re.findall(r"[A-Za-z0-9'\u2019-]+", candidate or "")]
    prior_words = [word.lower() for word in re.findall(r"[A-Za-z0-9'\u2019-]+", prior or "")]
    if len(cand_words) < size or len(prior_words) < size:
        return False
    prior_runs = {
        " ".join(prior_words[index : index + size])
        for index in range(len(prior_words) - size + 1)
    }
    return any(
        " ".join(cand_words[index : index + size]) in prior_runs
        for index in range(len(cand_words) - size + 1)
    )


def is_redundant_paragraph(candidate: str, prior: str) -> bool:
    """True when a paragraph mostly restates material already printed.

    The threshold is deliberately conservative: a paragraph needs at least six
    content words, and it is kept whenever it introduces a new number or proper
    name. This targets model padding ("the move is seen as a significant
    change...") without removing genuinely new detail.
    """
    words = _content_words(candidate)
    if len(words) < 6:
        return False
    prior_words = set(_content_words(prior))
    if not prior_words:
        return False
    novel = [word for word in words if word not in prior_words]
    if len(novel) / len(words) >= 0.5:
        return False
    return _fresh_facts(candidate, prior) == 0


def _channel_said(match: re.Match[str]) -> str:
    return ("The" if match.group(0)[:1].isupper() else "the") + " channel said"


def strip_output_decor(text: str) -> str:
    """Return a clean, printable form of a source-derived string.

    The transformation is deliberately conservative: it removes broadcast
    furniture and normalises spacing, never rewrites wording or drops a
    factual clause.
    """
    value = unicodedata.normalize("NFKC", text or "")
    value = _LEADING_DECOR.sub("", value)
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
    value = _REPEATED_COMMA.sub(",", value)
    for wrong, right in _COMMON_TYPOS.items():
        value = re.sub(rf"\b{wrong}\b", right, value, flags=re.I)
    return value.strip(" \t-|\u2013\u2014")


def clean_sentence(text: str) -> str:
    """Clean one sentence and guarantee terminal punctuation."""
    value = strip_output_decor(text)
    if value and value[-1] not in ".!?\u2026\"'\u201d":
        value += "."
    return value


def repair_prose(text: str) -> str:
    """Repair deterministic attribution and punctuation defects in drafted copy.

    The model occasionally produces strings such as ``the channel reported
    noted`` or ``according to a recent message from the channel reported``.
    Both are grammar failures, not additional sourcing. This function removes
    the redundant clause and leaves the factual statement intact. It never
    invents an actor and never changes a number, name, date or quotation.
    """
    value = strip_output_decor(text)
    value = _REDUNDANT_SOURCE_CLAUSE.sub("", value)
    value = _CHANNEL_REPORTING.sub("the channel said that", value)
    value = _DOUBLE_ATTRIBUTION.sub(r"\1", value)
    value = _CHANNEL_ATTRIBUTION.sub(_channel_said, value)
    value = _REDUNDANT_TRAILING_ATTRIBUTION.sub("", value)
    value = _EMPTY_PARENS.sub("", value)
    value = _DANGLING_COMMA.sub(r"\1", value)
    value = _REPEATED_COMMA.sub(",", value)
    value = _MULTISPACE.sub(" ", value)
    value = _SPACE_BEFORE_PUNCT.sub(r"\1", value)
    value = re.sub(r"\s{2,}", " ", value)
    return value.strip(" \t-|\u2013\u2014")


def is_broadcast_furniture(text: str) -> bool:
    """True when source text still carries Telegram broadcast decoration."""
    return bool(
        _DECOR.search(text or "")
        or _HANDLE.search(text or "")
        or _STATUS_UPPER.search(text or "")
    )


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
