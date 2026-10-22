from __future__ import annotations

import re

from ..constants import STOCK_PHRASES
from .entities import sentence_split


def style_lint(title: str, standfirst: str, body: list[str], *, genre: str = "news") -> dict:
    text = " ".join(body)
    sentences = sentence_split(text)
    words = re.findall(r"[A-Za-z']+", text)
    issues: list[dict] = []
    lower = text.lower()
    found = [phrase for phrase in STOCK_PHRASES if phrase in lower]
    if found:
        issues.append(
            {
                "code": "stock_phrase",
                "severity": "review",
                "message": "Stock transition phrases found: " + ", ".join(found),
            }
        )
    if sentences:
        avg = sum(len(s.split()) for s in sentences) / len(sentences)
        if avg > 36:
            issues.append({"code": "long_sentences", "severity": "note", "message": f"Average sentence length is {avg:.1f} words."})
        if avg < 10 and len(sentences) > 3:
            issues.append({"code": "short_sentences", "severity": "note", "message": f"Average sentence length is {avg:.1f} words."})
    starts: dict[str, int] = {}
    for sentence in sentences:
        first = " ".join(sentence.split()[:2]).lower()
        starts[first] = starts.get(first, 0) + 1
    repeated = [key for key, count in starts.items() if count >= 3]
    if repeated:
        issues.append({"code": "repeated_openings", "severity": "note", "message": "Repeated sentence openings: " + ", ".join(repeated)})
    if "!" in text:
        issues.append({"code": "exclamation", "severity": "review", "message": "Exclamation marks are not appropriate for this newspaper style."})
    for paragraph in body:
        if len(paragraph.split()) > 120:
            issues.append({"code": "long_paragraph", "severity": "note", "message": "A paragraph exceeds 120 words."})
            break
    if genre in {"news", "analysis"} and "editorial:" in (title + " " + text).lower():
        issues.append({"code": "genre_mismatch", "severity": "review", "message": "News/analysis draft contains an editorial label."})
    return {
        "issues": issues,
        "sentence_count": len(sentences),
        "word_count": len(words),
        "average_sentence_words": round(sum(len(s.split()) for s in sentences) / len(sentences), 1) if sentences else 0,
        "stock_phrases": found,
        "repeated_openings": repeated,
    }
