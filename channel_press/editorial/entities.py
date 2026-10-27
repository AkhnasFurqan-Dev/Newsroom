from __future__ import annotations

import json
import re

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to", "for", "from",
    "with", "by", "as", "is", "are", "was", "were", "be", "been", "being", "that", "this",
    "these", "those", "it", "its", "he", "she", "they", "them", "his", "her", "their",
    "said", "says", "also", "had", "has", "have", "will", "would", "could", "should",
    "after", "before", "during", "than", "then", "into", "over", "under", "about", "more",
    "not", "no", "who", "which", "what", "when", "where", "how", "while", "one", "two",
    "three", "four", "five", "six", "seven", "eight", "nine", "ten", "percent", "per",
    "cent", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december", "local", "time", "official", "officials", "report",
    "reported", "reports", "according", "separately", "some", "new", "last", "next", "year",
    "years", "day", "days", "week", "weeks", "month", "months", "morning", "evening", "night",
    "today", "yesterday", "tomorrow", "there", "here", "up", "out", "off", "very", "still",
}

PLACES = [
    "Chaman", "Bannu", "Swat", "Chilas", "Taftan", "Quetta", "Peshawar", "Lahore", "Multan",
    "Sukkur", "Gwadar", "Gilgit", "Muzaffarabad", "Dera Ismail Khan", "Abbottabad", "Sialkot",
    "Bahawalpur", "Sargodha", "Hyderabad", "Mardan", "Khyber Pakhtunkhwa", "Balochistan",
    "Punjab", "Sindh", "Gilgit-Baltistan", "Islamabad", "Karachi", "Pakistan", "Iran",
    "Afghanistan", "India", "China", "Tehran", "Kabul", "Delhi", "Washington", "Beijing",
]

NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "dozens", "hundreds", "thousands", "million", "billion",
}


def tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 2]


def token_set(text: str) -> set[str]:
    return set(tokens(text))


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def proper_nouns(text: str) -> set[str]:
    found = set()
    for phrase in PLACES:
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            found.add(phrase)
    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b", text):
        value = match.group(1).strip()
        if value.lower() not in STOPWORDS and len(value) > 2:
            found.add(value)
    return found


def numbers(text: str) -> set[str]:
    result = set(re.findall(r"\b\d+(?:\.\d+)?\b", text))
    for word in NUMBER_WORDS:
        if re.search(rf"\b{word}\b", text.lower()):
            result.add(word)
    return result


def locations(text: str) -> set[str]:
    return {place for place in PLACES if re.search(rf"\b{re.escape(place)}\b", text)}


def sentence_split(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    if not text:
        return []
    # Protect common abbreviations.
    protected = text.replace("Dr.", "Dr<dot>").replace("Mr.", "Mr<dot>").replace("Ms.", "Ms<dot>")
    protected = re.sub(r"\b([A-Z])\.", r"\1<dot>", protected)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9“\"'])", protected)
    return [p.replace("<dot>", ".").strip() for p in parts if p.strip()]


def headline_from_text(text: str, max_len: int = 92) -> str:
    first = sentence_split(text)
    base = first[0] if first else text
    base = re.sub(r"^(Update|Correction)\s*:\s*", "", base, flags=re.I).strip()
    base = re.sub(r"^(Analysis|Editorial)\s*:\s*", "", base, flags=re.I).strip()
    words = base.split()
    if len(base) > max_len:
        base = " ".join(words[: max_len // 6]) + "…"
    if base:
        base = base[0].upper() + base[1:]
    return base.rstrip(".")


def parse_entity_json(raw: str | None) -> dict:
    """Read a story's entity payload, tolerating both stored shapes.

    Older clusters store a bare list of names; newer ones store a dict with
    places, risk tags and status detail. Readers must handle either without
    failing a layout build.
    """
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        names = [str(item) for item in data if isinstance(item, str) and item.strip()]
        return {"names": names, "places": names}
    return {}


def infer_section(text: str, topic: str | None = None) -> str:
    from ..constants import SECTION_KEYWORDS

    mapping = {
        "conflict": "conflict",
        "defence": "defence",
        "international": "international",
        "regional": "regional",
        "national": "national",
        "economy": "national",
        "analysis": "analysis",
        "opinion": "opinion",
        "briefs": "briefs",
    }
    if topic and topic in mapping:
        return mapping[topic]
    scores: dict[str, int] = {}
    lower = text.lower()
    for section, words in SECTION_KEYWORDS.items():
        scores[section] = sum(1 for word in words if word in lower)
    best = max(scores, key=lambda key: scores[key])
    return best if scores[best] > 0 else "national"
