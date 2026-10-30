"""Independent deterministic editorial-quality pass.

The drafting model writes; this module judges the result with rules that never
call a model. It is deliberately separate from the evidence audit in
:mod:`channel_press.editorial.audit`: that pass answers "can this sentence be
traced to the source?", while this one answers "is this a usable newspaper
article that keeps reported fact apart from the writer's opinion?".

Findings are advisory unless they concern fabricated attribution or an opinion
leak in a news report, which must reach a human before release.
"""

from __future__ import annotations

import re
from collections import Counter

from ..constants import STOCK_PHRASES
from .entities import sentence_split
from .moderation import attribution_issues

QUALITY_VERSION = "quality-2026-10-26-v1"

_WORD = re.compile(r"[A-Za-z0-9'\u2019\-]+")
_CLICKBAIT = re.compile(
    r"(?i)\b(you\s+won'?t\s+believe|shocking|jaw[-\s]?dropping|must\s+see|"
    r"goes\s+viral|internet\s+reacts|here'?s\s+why|what\s+happened\s+next|"
    r"this\s+is\s+why|the\s+truth\s+about)\b"
)
_OPINION_MARKER = re.compile(
    r"(?i)\b(?:i|we)\s+(?:believe|think|feel|argue|urge|demand|insist)\b|"
    r"\b(?:shameful|outrageous|disgraceful|appalling|heroic|cowardly|corrupt|"
    r"incompetent|brutal|senseless|unjust|unconscionable)\b|"
    r"\b(?:must|should)\b"
)
_ATTRIBUTION = re.compile(
    r"(?i)\b(?:said|says|stated|according\s+to|claimed|alleged|quoted|told|"
    r"announced|confirmed|denied|argued|wrote|posted)\b|[\"\u201c\u201d]"
)
_CERTAINTY = re.compile(
    r"(?i)\b(?:reportedly|allegedly|unconfirmed|unverified|purportedly|"
    r"could\s+not\s+be\s+independently\s+verified)\b"
)


def _words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _shingles(tokens: list[str], size: int = 8) -> Counter[str]:
    return Counter(
        " ".join(tokens[index : index + size])
        for index in range(max(0, len(tokens) - size + 1))
    )


def _finding(code: str, severity: str, message: str, **extra: object) -> dict:
    return {"code": code, "severity": severity, "message": message, **extra}


def _opinion_leaks(body: list[str]) -> list[dict]:
    """Sentences that editorialise without attribution, in a factual genre."""
    leaks: list[dict] = []
    for paragraph in body:
        for sentence in sentence_split(paragraph):
            if not _OPINION_MARKER.search(sentence):
                continue
            if _ATTRIBUTION.search(sentence) or _CERTAINTY.search(sentence):
                continue
            if len(_words(sentence)) < 4:
                continue
            leaks.append({"sentence": sentence.strip()[:300]})
            if len(leaks) >= 5:
                return leaks
    return leaks


def _repeated_phrases(body: list[str]) -> list[str]:
    """Verbatim eight-word runs printed more than once across the body."""
    tokens = [token.lower() for paragraph in body for token in _words(paragraph)]
    repeated = [phrase for phrase, count in _shingles(tokens).items() if count >= 2]
    return sorted(repeated, key=lambda phrase: (-len(phrase), phrase))[:3]


def _verbatim_source_runs(body: list[str], source_text: str, *, size: int = 12) -> list[str]:
    """Long word runs copied verbatim from the source evidence.

    A newspaper may quote a short phrase, but a paragraph lifted wholesale from
    a Telegram post is not synthesis. This is a note for the editor, not an
    automatic rewrite: a genuine direct quotation is an editorial decision.
    """
    source_tokens = [token.lower() for token in _words(source_text)]
    if len(source_tokens) < size:
        return []
    source_runs = {
        " ".join(source_tokens[index : index + size])
        for index in range(len(source_tokens) - size + 1)
    }
    body_tokens = [token.lower() for token in _words("\n".join(body))]
    runs: list[str] = []
    for index in range(max(0, len(body_tokens) - size + 1)):
        candidate = " ".join(body_tokens[index : index + size])
        if candidate in source_runs and candidate not in runs:
            runs.append(candidate)
        if len(runs) >= 3:
            break
    return runs


def _repeated_openings(body: list[str]) -> list[str]:
    starts: Counter[str] = Counter()
    for paragraph in body:
        for sentence in sentence_split(paragraph):
            words = sentence.split()
            if len(words) >= 2:
                starts[" ".join(words[:2]).lower()] += 1
    return sorted(key for key, count in starts.items() if count >= 3)


def quality_review(
    *,
    title: str,
    deck: str,
    standfirst: str,
    body: list[str],
    genre: str,
    publication_names: list[str],
    source_text: str = "",
) -> dict:
    """Return a deterministic quality report for one drafted article."""
    findings: list[dict] = []
    clean_title = (title or "").strip()
    clean_body = [paragraph.strip() for paragraph in body if (paragraph or "").strip()]
    body_text = "\n\n".join(clean_body)
    body_words = _words(body_text)
    lead_words = _words(clean_body[0]) if clean_body else []
    paragraph_words = [len(_words(paragraph)) for paragraph in clean_body]

    # --- headline -----------------------------------------------------------
    if not clean_title:
        findings.append(_finding("empty_headline", "block_publish", "The article has no headline."))
    else:
        if len(clean_title) > 95:
            findings.append(
                _finding(
                    "long_headline",
                    "review",
                    f"Headline is {len(clean_title)} characters; tabloid headlines should fit the column measure.",
                )
            )
        letters = [char for char in clean_title if char.isalpha()]
        if len(letters) >= 12 and all(char.isupper() for char in letters):
            findings.append(_finding("all_caps_headline", "review", "Headline is set in all capitals."))
        if clean_title.endswith("."):
            findings.append(_finding("headline_period", "review", "Headline ends with a full stop."))
        clickbait = _CLICKBAIT.search(clean_title)
        if clickbait:
            findings.append(
                _finding(
                    "clickbait_headline",
                    "review",
                    f"Headline uses promotional phrasing: '{clickbait.group(0)}'.",
                )
            )
        if "?" in clean_title and genre == "news":
            findings.append(
                _finding("question_headline", "note", "A news headline is phrased as a question.")
            )
        if len(_words(clean_title)) < 3:
            findings.append(_finding("thin_headline", "review", "Headline is too short to identify the development."))

    # --- lead and structure -------------------------------------------------
    if not clean_body:
        findings.append(_finding("empty_body", "block_publish", "The article has no body text."))
    else:
        if len(lead_words) < 8 and genre != "brief":
            findings.append(
                _finding("thin_lead", "review", "The opening paragraph does not state a complete development.")
            )
        if len(lead_words) > 80:
            findings.append(_finding("long_lead", "note", "The opening paragraph exceeds 80 words."))
        for index, count in enumerate(paragraph_words, start=1):
            if count > 130:
                findings.append(
                    _finding(
                        "long_paragraph",
                        "note",
                        f"Paragraph {index} has {count} words; split it for print.",
                    )
                )
                break
        if len(clean_body) == 1 and genre not in {"brief", "editor_note"} and len(body_words) > 90:
            findings.append(
                _finding("single_paragraph", "note", "The article is a single long paragraph.")
            )
        minimum = {"brief": 20, "news": 70, "analysis": 110, "opinion": 110, "editor_note": 40}.get(genre, 50)
        if len(body_words) < minimum:
            findings.append(
                _finding(
                    "short_body",
                    "review",
                    f"{genre.title()} article has {len(body_words)} words; expected at least {minimum}.",
                )
            )
        if len(body_words) > 2200:
            findings.append(_finding("overlong_body", "note", "Article exceeds 2,200 words."))

    # --- repetition and stock phrasing -------------------------------------
    repeated = _repeated_phrases(clean_body)
    if repeated:
        findings.append(
            _finding(
                "repeated_phrase",
                "review",
                "The same wording is printed more than once: " + "; ".join(f"'{phrase}'" for phrase in repeated),
                phrases=repeated,
            )
        )
    openings = _repeated_openings(clean_body)
    if openings:
        findings.append(
            _finding(
                "repeated_openings",
                "note",
                "The same sentence opening is used three or more times: " + ", ".join(openings),
                openings=openings,
            )
        )
    verbatim = _verbatim_source_runs(clean_body, source_text) if source_text else []
    if verbatim:
        findings.append(
            _finding(
                "verbatim_source_text",
                "note",
                "A long run of wording is copied verbatim from the source; verify this is synthesis rather than paraphrase-by-padding.",
                phrases=verbatim,
            )
        )
    lower = body_text.lower()
    stock = [phrase for phrase in STOCK_PHRASES if phrase in lower]
    if stock:
        findings.append(
            _finding(
                "stock_phrase",
                "note",
                "Stock transition phrasing: " + ", ".join(stock),
                phrases=stock,
            )
        )
    # Repeating the standfirst or deck as the first body sentence wastes the
    # best space on the page.
    if clean_body and standfirst:
        first = _normalise(clean_body[0])
        if first and _normalise(standfirst) and _normalise(standfirst) in first:
            findings.append(_finding("redundant_standfirst", "note", "Standfirst repeats the opening paragraph."))
    if clean_body and deck:
        first = _normalise(clean_body[0])
        if first and _normalise(deck) and _normalise(deck) in first:
            findings.append(_finding("redundant_deck", "note", "Deck repeats the opening paragraph."))

    # --- attribution and opinion -------------------------------------------
    for name in publication_names:
        for issue in attribution_issues(f"{clean_title} {body_text}", name):
            findings.append(
                _finding(
                    "attribution",
                    "review",
                    f"Publication identity is written as a person or eyewitness: {issue}.",
                )
            )
    leaks: list[dict] = []
    if genre in {"news", "analysis"}:
        leaks = _opinion_leaks(clean_body)
        for leak in leaks:
            findings.append(
                _finding(
                    "opinion_leak",
                    "review",
                    "Unattributed opinion appears in a factual article: " + leak["sentence"][:160],
                    sentence=leak["sentence"],
                )
            )
    if genre == "news":
        certainty = sorted(set(match.group(0) for match in _CERTAINTY.finditer(body_text)))
        if certainty:
            findings.append(
                _finding(
                    "uncertainty_label_present",
                    "note",
                    "Draft already marks uncertainty: " + ", ".join(certainty) + ". Verify each label is justified.",
                )
            )

    # --- source-facing sanity ----------------------------------------------
    if source_text and body_words:
        overlap = set(token.lower() for token in body_words) & set(token.lower() for token in _words(source_text))
        if len(overlap) == 0:
            findings.append(
                _finding(
                    "no_source_overlap",
                    "review",
                    "Draft shares no wording with the source evidence; verify it was not invented.",
                )
            )

    severities = {finding["severity"] for finding in findings}
    if "block_publish" in severities:
        status = "block"
    elif "review" in severities:
        status = "review"
    else:
        status = "pass"
    return {
        "version": QUALITY_VERSION,
        "status": status,
        "findings": findings,
        "headline_characters": len(clean_title),
        "headline_words": len(_words(clean_title)),
        "lead_words": len(lead_words),
        "body_words": len(body_words),
        "paragraph_words": paragraph_words,
        "opinion_leaks": leaks,
        "repetitions": repeated,
        "repeated_openings": openings,
        "verbatim_source_runs": verbatim,
        "stock_phrases": stock,
    }
