"""Deterministic post classification, language handling and moderation.

Every source post is classified before story drafting so that low-information
material (greetings, emoji, promotions, link-only reposts, abuse with no report)
is excluded with an explicit, persisted reason, while short but meaningful
alerts — casualty notices, corrections, emergency warnings, informal Roman Urdu
updates — remain eligible.

Processing order (see BUILD_SPECIFICATION §"language handling"):
    1. the original source text is preserved untouched by callers;
    2. a normalized analysis form is derived here;
    3. language families (English / Urdu script / Roman Urdu / mixed) are detected;
    4. profanity and abusive language are detected, including obfuscated forms;
    5. meaningful news signals are scored;
    6. translation happens upstream in the story layer;
    7. English drafting works only from the evidence bundle.

Everything in this module is deterministic and rule-based; no network or model
call may gate a safety decision here. The word lists are small, self-authored
and versioned — no third-party profanity corpus is copied into this repository.

All source text flowing into this module is untrusted data. Nothing in it is
ever executed or treated as an instruction (see :func:`classify_message`,
which neutralises embedded prompt-injection sentences).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..ingestion.adapters import NormalizedMessage

CLASSIFIER_VERSION = "post-moderation-2026-10-24-v2"

CLASSIFICATION_LABELS = {
    "editorially_relevant": "Editorially relevant",
    "context_only": "Context only (no reportable development)",
    "promotion": "Promotion / channel call",
    "duplicate": "Duplicate or forwarded copy",
    "empty": "No content",
    "low_information": "Low information",
    "profanity_only": "Profanity or abuse without news value",
    "unsupported_media_only": "Unsupported media without usable caption",
    "language_needs_review": "Unrecognised script — needs language review",
    "rights_blocked": "Blocked: no active rights grant",
}

# Editor overrides: explicit decisions that survive re-imports and classifier
# upgrades. Value = (inclusion_status, exclusion_reason, classification).
EDITOR_OVERRIDES = {
    "include": ("included", "", "editorially_relevant"),
    "exclude": ("excluded", "editor_excluded", "low_information"),
    "context_only": ("excluded", "editor_context_only", "context_only"),
    "promotion": ("excluded", "editor_promotion", "promotion"),
    "profanity": ("excluded", "editor_profanity", "profanity_only"),
}

# Severities ordered from least to most severe. ``uncertain`` sits above
# ``none``: an obfuscated match still deserves human eyes, just less certainty.
_PROFANITY_ORDER = [
    "none",
    "uncertain",
    "mild",
    "strong",
    "sexual_or_explicit",
    "targeted_abuse",
    "threat",
    "hate_or_dehumanizing",
]

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_ZERO_WIDTH = "\u200b\u200c\u200d\ufeff"
_LEET_MAP = {"@": "a", "4": "a", "0": "o", "1": "i", "3": "e", "5": "s", "$": "s", "!": "i", "7": "t", "8": "b"}
_VOWELS = "aeiou"
_URL = re.compile(r"(?:https?://|www\.)\S+|t\.me/\S+", re.IGNORECASE)
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF\u2B00-\u2BFF\uFE0F]+"
)


def strip_urls(text: str) -> str:
    return _URL.sub(" ", text or "")


def normalize_for_analysis(text: str) -> str:
    """NFKC + casefold + punctuation/whitespace cleanup for signal matching.

    This form never replaces the original source text; it exists only for
    deterministic analysis. Repeated letters are preserved here (news-signal
    matching wants them); profanity matching applies its own reductions.
    """
    value = unicodedata.normalize("NFKC", text or "").casefold()
    for zero in _ZERO_WIDTH:
        value = value.replace(zero, "")
    value = re.sub(r"[^\w\s\u0600-\u06ff]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _skeleton(token: str) -> str:
    """Vowel-stripped, run-reduced consonant skeleton for fuzzy matching."""
    out = []
    previous = ""
    for char in token:
        if char in _VOWELS:
            previous = ""
            continue
        if char == previous:
            continue
        out.append(char)
        previous = char
    return "".join(out)


def _match_variants(token: str) -> set[str]:
    """Candidate forms of a token for dictionary lookup (analysis only)."""
    cleaned = re.sub(r"[^0-9a-z\u0600-\u06ff]", "", token.casefold())
    leet = "".join(_LEET_MAP.get(char, char) for char in cleaned)
    variants = {cleaned, leet}
    for form in list(variants):
        variants.add(re.sub(r"(.)\1+", r"\1\1", form))  # run -> pairs ("fuuck"->"fuuk")
        variants.add(re.sub(r"(.)\1+", r"\1", form))  # run -> single ("haraaami"->"harami")
    for form in list(variants):
        variants.add(_skeleton(form))
    return {v for v in variants if v}


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------


def detect_language_family(text: str) -> str:
    """Best-effort family label: en / ur / roman_ur / mixed_ur_en / other."""
    if not (text or "").strip():
        return "en"
    arabic = sum("\u0600" <= char <= "\u06ff" for char in text)
    latin = sum(char.isascii() and char.isalpha() for char in text)
    letters = sum(char.isalpha() for char in text)
    if letters and not arabic and not latin:
        return "other"  # a recognised script, but not one this desk edits
    if arabic and latin:
        return "mixed_ur_en"
    if arabic:
        return "ur"
    roman_markers = {
        "hai", "hain", "mein", "nahin", "nahi", "aur", "kya", "kaise", "kiya", "tha",
        "thi", "shaheed", "zakhmi", "hamla", "hamlay", "waqia", "waqiya", "fauj", "wazir",
        "shehar", "zila", "gaya", "gayi", "diya", "diye", "bhai", "janab", "sahib",
        "kuch", "koi", "phir", "lekin", "magar", "kyun", "kitna", "kitne", "batao",
    "bandish", "tijarat", "tajir", "nuqsan", "tanzim", "ajlas", "ajlaas", "dharna",
    "muzahira", "ahtijaj", "wazart", "numainda", "aqwam", "bain-ul-aqwam",
    }
    tokens = set(normalize_for_analysis(text).split())
    return "roman_ur" if len(tokens & roman_markers) >= 1 else "en"


# ---------------------------------------------------------------------------
# News signals
# ---------------------------------------------------------------------------

# Strong signals carry a verifiable development: casualty, official action,
# location/time anchors, corrections, numbers and named actors. Urdu and Roman
# Urdu keyword sets deliberately cover the same concepts so a short Urdu/Roman
# Urdu alert is not discarded just because it is informal or transliterated.
# ``_EVENT_WORDS`` is word-only: a bare number is evidence of *something* but
# not by itself proof of a report, so promotions or "good morning 2026" cannot
# smuggle themselves past the filter with a digit.
_EVENT_WORDS = (
    r"attack|attacks|blast|blasts|strikes?|struck|killed|killing|dead|deaths?|dies|died|injured|injuries|wounded|wounding|"
    r"casualties|fatalities|arrested|arrest|arrests|raid|raids|shooting|explosion|fire|fires|flood|floods|earthquake|quake|"
    r"accident|crash|collapse|derail|warning|warned|emergency|evacuat\w*|curfew|protest|protests|rally|rallys|"
    r"statement|statements|announced|announces|confirmed|denied|denies|revealed|reported|reports|according|claims?|claimed|alleged|alleges|"
    r"accused|accuses|criticised|criticized|condemned|demanded|demands|called|says|said|told|"
    r"court|courts|police|army|military|government|minister|ministry|president|governor|assembly|senate|election|elections|"
    r"highway|roads?|bridge|border|district|province|hospital|school|market|power|outage|"
    r"correction|corrects|clarification|clarifies|reopens?|resumes?|deploys?|deployed|summons|summoned|indict|convict|sentence|sentenced|"
    r"won|wins|victory|championship|appointed|resigned|elected|inaugurated|launched|approved|budget|tax|inflation|dollar|rupee|storm|heatwave|"
    r"parliament|committee|committees|authorities|agency|agencies|summit|talks|agreement|contract|deal|"
    r"allocated|allocation|funds|review|expected|program|programs|project|projects|survey|study|"
    r"closes?|closure|restricts?|restriction|suspend|suspending|ban|banned|bans|"
    r"protesters|demonstrators|dharna|march|marches|kidnap|kidnapped|missing persons|ransom|smuggl\w*|seized|seizure|"
    r"official|officials|notification|notice|circular|order|orders|advisory|alert|press release|gazette|"
    r"injured|rescued|rescue|recovered|recovery|damaged|destroyed|torched|abducted|abduction|fired|gunfire|grenade|"
    r"chief|leader|spokesman|spokesperson|commissioner|inspector|deputy|secretary|director|chairman|"
    r"health|hospitalised|hospitalized|treatment|vaccination|campaign|drive|operation|offensive|ceasefire|truce"
)
_STRONG_SIGNAL = re.compile(
    r"(?i)\b(" + _EVENT_WORDS + r")\b|"
    r"\b\d{1,4}(?:[\.,]\d+)?\b|"
    r"\b(?:19|20)\d{2}\b|"
    # Urdu-script strong keywords
    r"حملہ|حملے|دھماکہ|دھماکے|شہید|زخمی|زخمیوں|گرفتار|تفتیش|فیصلہ|عدالت|وزیر|اعلان|تاکید|انکار|"
    r"سیلاب|زلزلہ|حادثہ|واقعہ|واقعے|انتباہ|ہدایت|فوج|پولیس|صدارت|اسمبلی|انتخابات|سڑک|پل|حدود|"
    r"تصحیح|روزنامہ|وفات|"
    r"بندش|راستہ|راستے|تجارت|تجارتی|تاجر|تاجروں|نقصان|تنظیم|مطالبہ|الزام|اجلاس|وزارت|"
    r"مظاہرہ|دھرنا|گمشدہ|اجرت|زیر قیادت|سرقومہ|ادارہ|شعبہ|اطلاع|نوٹس|ہدایات|کارروائی|ریسکیو|امداد",
    re.IGNORECASE,
)
_EVENT_SIGNAL = re.compile(
    r"(?i)\b(" + _EVENT_WORDS + r")\b|"
    r"حملہ|حملے|دھماکہ|دھماکے|شہید|زخمی|زخمیوں|گرفتار|تفتیش|فیصلہ|عدالت|وزیر|اعلان|تاکید|انکار|"
    r"سیلاب|زلزلہ|حادثہ|واقعہ|واقعے|انتباہ|ہدایت|فوج|پولیس|صدارت|اسمبلی|انتخابات|سڑک|پل|حدود|"
    r"تصحیح|روزنامہ|وفات|بندش|راستہ|راستے|تجارت|تجارتی|تاجر|تاجروں|نقصان|تنظیم|مطالبہ|الزام|اجلاس|وزارت|"
    r"مظاہرہ|دھرنا|گمشدہ|اجرت|زیر قیادت|سرقومہ|ادارہ|شعبہ|اطلاع|نوٹس|ہدایات|کارروائی|ریسکیو|امداد",
    re.IGNORECASE,
)
# Institutions alone are not a report. Without this distinction, "fuck the
# police" would count "police" as an event and slip past the abuse-only gate.
_ACTOR_ONLY_SIGNAL = re.compile(
    r"(?i)\b(?:police|army|military|government|minister|ministry|president|governor|"
    r"assembly|senate|courts?|officials?|chief|leader|spokesm[ae]n|spokesperson|"
    r"commissioner|inspector|deputy|secretary|director|chairman|authorities|"
    r"agenc(?:y|ies)|parliament|committees?)\b|"
    r"پولیس|فوج|حکومت|وزیر|وزارت|صدر|گورنر|اسمبلی|عدالت|پارلیمنٹ|کمیٹی|ترجمان|"
    r"افسر|سربراہ|چیئرمین|ڈپٹی|سیکرٹری|ڈائریکٹر|ایجنسی|ادارہ",
    re.IGNORECASE,
)
# Roman Urdu strong keywords (transliteration-aware, spelling variants).
_ROMAN_STRONG = {
    "hamla", "hamlay", "hamlon", "dhamaka", "dhamakay", "shaheed", "shaheeden",
    "zakhmi", "zakhmiyon", "halak", "halakat", "ghayal", "giraftar", "giraftari",
    "faisla", "faislay", "adalat", "wazir", "wazart", "sadr", "elam", "elaan",
    "taiid", "inkaar", "fauj", "fauji", "pulis", "police", "sapai", "zanjeer",
    "zalzala", "sayl", "saylal", "hadisa", "hadisay", "waqia", "waqiya", "waqiat",
    "ehtiyat", "itla", "itlaa", "khabar", "tasheeh", "sarak", "pul",
    "sarhad", "intekhab", "intekhabat", "assembly", "senate", "nazar bandi",
    "marees", "mareeson", "ambulance", "firebrigade",
    "kaha", "kehna", "kehta", "ilzaam", "ilzamat", "mulzim", "mulziman",
    "dhamki", "dhamkian", "thana", "thane", "hukumat", "hukoomat", "sarkar",
    "tahqeeq", "muqadma", "jalsa", "juloos", "dharne", "ehtijaj", "etiraz",
    "chhapa", "chhape", "chhapi", "chhapay", "riport", "rescue", "amdad",
    "hadeesa", "halaat", "mausam", "barish", "baarish", "toofan",
}
# Weak signals ("breaking", "update") require a strong companion; alone they
# carry no information and must not smuggle a post past the filter.
_WEAK_SIGNAL = re.compile(
    r"(?i)\b(breaking|breaking news|update|updates|latest|watch this space|watch (?:this|the) video|"
    r"developing|news update|stay tuned|more to follow)\b"
)

_GREETING_SIGNAL = re.compile(
    r"(?i)^\s*(?:"
    r"hi|hello|hey|howdy|salam|salaam|assalam[\s-]?o[\s-]?alaikum|assalamualaikum|aslam[\s-]?o[\s-]?alaikum|"
    r"good\s*(?:morning|afternoon|evening|night)|subha\s*bakhair|shab\s*bakhair|"
    r"khuda\s*hafiz|allah\s*hafiz|jazak[\s-]?allah|shukriya|thanks?|thank\s*you|"
    r"kaise\s*(?:hain|ho)|kaisay\s*(?:hain|ho)|kya\s*haal|theek\s*(?:hain|ho)|khairiyat|"
    r"dua(?:on|o)?\s*me[n]?\s*yaad\s*rakh(na|na)|yaad\s*rakhna|"
    r"congratulations?|congrats|mubarak\s*ho|bohat\s*mubarak|condolences?|"
    r"rip|may\s*(?:he|she|they|allah)\s*(?:bless|grant|rest)|"
    r"take\s*care|apna\s*khyal\s*rakhna|allah\s*paak\s*hifz\s*me\s*rakhe"
    r")[.!?,\s]*$"
)
# Prefix greetings: "Assalam o alaikum dosto, duaon mein yaad rakhna" and
# "Hello everyone, quick update..." are greetings even with a trailing vocative.
_GREETING_PREFIX = re.compile(
    r"(?i)^\s*(?:assalam[\s-]?o[\s-]?alaikum|aslam[\s-]?o[\s-]?alaikum|salam|salaam|"
    r"السلام\s*علیکم|مرحوم|hello|hi|hey|good\s*(?:morning|afternoon|evening|night)|"
    r"khuda\s*hafiz|allah\s*hafiz|subha\s*bakhair|shab\s*bakhair)"
)
_GREETING_URDU = re.compile(
    r"^\s*(?:السلام\s*(?:علیکم|وعلیکم\s*السلام)?|صلی\s*اللہ\s*علیہ\s*وسلم|اللہ\s*حافظ|خدا\s*حافظ|"
    r"شکریہ|مبارک\s*ہو|تسلیت|مرحوم|اللہ\s*انہیں|دعاوں\s*میں\s*یاد\s*رکھیں|امید\s*ہے\s*آپ\s*خیریت\s*سے\s*ہوں|"
    r"کیسے\s*ہیں|ٹھیک\s*ہیں|آپ\s*کا\s*کیا\s*حال|خیریت)\s*[.!؟,\s]*$"
)
_SLOGAN_SIGNAL = re.compile(
    r"(?i)^\s*(?:پاکستان\s*زندہ\s*اد|لابیک|قوم\s*ایک|نعرہٴ\s*تکبیر|"
    r"pakistan\s*zindabad|labbaik|naara[\s-]?e[\s-]?takbeer|long\s*live\s*\w+|"
    r"we\s*shall\s*overcome|power\s*to\s*the\s*people)[.!?\s]*$"
)
_PROMOTION = re.compile(
    r"(?i)\b(join|follow|subscribe|subscribed|like|share|donate|donation|advertise|advertisement|"
    r"promo|promotion|sponsored|discount|offer|sale|buy now|order now|click the link|"
    r"join[\s-]?(our|the)|forward(?:ed)?\s+to|tag\s+(your|a)\s+friend)\b|t\.me/"
)
_ENGAGEMENT_BAIT = re.compile(
    r"(?i)\b(like\s+and\s+share|share\s+if|comment\s+below|repost\s+if|retweet|"
    r"who\s+else\s+thinks|100[\s%]|copy[\s-]?paste\s+and\s+share|like\s+if\s+you)\b"
)

# ---------------------------------------------------------------------------
# Profanity / abuse (self-authored, versioned lists — no third-party corpus)
# ---------------------------------------------------------------------------

# English + Roman Urdu full forms. Severity/category per entry.
_PROFANITY_FORMS = {
    "fuck": ("strong", "explicit"),
    "fucking": ("strong", "explicit"),
    "shit": ("strong", "explicit"),
    "shitty": ("strong", "explicit"),
    "bitch": ("targeted_abuse", "abuse"),
    "asshole": ("targeted_abuse", "abuse"),
    "arsehole": ("targeted_abuse", "abuse"),
    "bastard": ("mild", "abuse"),
    "basturd": ("mild", "abuse"),
    "damn": ("mild", "explicit"),
    "crap": ("mild", "explicit"),
    "dick": ("mild", "explicit"),
    "piss": ("mild", "explicit"),
    "pissed": ("mild", "explicit"),
    "fck": ("strong", "explicit"),  # obfuscated "fuck"
    "fk": ("strong", "explicit"),
    "fuk": ("strong", "explicit"),
    "fing": ("strong", "explicit"),  # "f***ing"
    "fking": ("strong", "explicit"),
    "fcuk": ("strong", "explicit"),
    "fcking": ("strong", "explicit"),
    "fcuking": ("strong", "explicit"),
    "sht": ("strong", "explicit"),  # obfuscated "shit"
    "bich": ("targeted_abuse", "abuse"),  # obfuscated "bitch"
    "harami": ("targeted_abuse", "abuse"),
    "haraami": ("targeted_abuse", "abuse"),
    "hrami": ("targeted_abuse", "abuse"),
    "kameena": ("targeted_abuse", "abuse"),
    "kamina": ("targeted_abuse", "abuse"),
    "kaminey": ("targeted_abuse", "abuse"),
    "kutti": ("targeted_abuse", "abuse"),
    "kutta": ("mild", "abuse"),
    "randi": ("targeted_abuse", "abuse"),
    "gandu": ("strong", "explicit"),
    "chutiya": ("strong", "explicit"),
    "chutyia": ("strong", "explicit"),
    "bhenchod": ("targeted_abuse", "sexual_or_explicit"),
    "bhanchod": ("targeted_abuse", "sexual_or_explicit"),
    "madarchod": ("targeted_abuse", "sexual_or_explicit"),
    "bsdk": ("targeted_abuse", "sexual_or_explicit"),
}
# Sexual-violence reporting is sensitive editorial material, not gratuitous
# profanity. Terms such as "rape" or "balatkar" appear in legitimate crime
# reports and must never be auto-redacted or auto-discarded by this layer.
_SENSITIVE_TERMS = {
    "rape", "raped", "gangrape", "balatkar", "naked", "nangi", "assault",
    "sexual assault", "molestation", "harassment",
}
# Multi-word profanity that token lookup alone cannot catch.
_PROFANITY_PHRASES = [
    re.compile(r"(?i)\b(?:son\s+of\s+a\s+bitch|mother\s*fucker|behen\s*chod|bhen\s*chod|madar\s*chod)\b"),
]
# Urdu-script profanity (matched against the cleaned token).
_PROFANITY_URDU = {
    "حرامی": ("targeted_abuse", "abuse"),
    "کمینہ": ("targeted_abuse", "abuse"),
    "کمینے": ("targeted_abuse", "abuse"),
    "کتی": ("targeted_abuse", "abuse"),
    "گالی": ("mild", "abuse"),
    "بینچود": ("targeted_abuse", "sexual_or_explicit"),
    "گندو": ("strong", "explicit"),
    "بلتکار": ("sexual_or_explicit", "sexual_or_explicit"),
}

# Threat phrases: abuse directed at a person with an implied or explicit
# promise of violence. Roman Urdu transliterations included.
_THREAT_PATTERNS = [
    re.compile(r"(?i)\b(kill|murder|shoot|destroy|finish|eliminate)\s+(you|u|him|her|them|ya)\b"),
    re.compile(r"(?i)\b(i|we|they)\s+(will|will)\s+(kill|murder|shoot|destroy)\b"),
    re.compile(r"(?i)\b(maar|maar[aeiou]?|dunga|doon|goli)\b[^.!?\n]{0,30}\b(tumhe|tumhen|tujhe|tera|teri|apko|you|u)\b"),
    re.compile(r"(?i)\b(jaan|jan)\s*se\s*(maar|khatam)\b"),
    re.compile(r"(?i)\btumhein?\s+(maar|goli)\b"),
    re.compile(r"(?i)\bdeath\s*threat\b"),
]
# Hate / dehumanizing: protected group compared to vermin/animals, or a whole
# group declared criminal/enemy. Deterministic and deliberately narrow.
_HATE_PATTERNS = [
    re.compile(
        r"(?i)\b(muslims?|hindus?|sikhs?|christians?|ahmadis?|shias?|sunnis?|pashtuns?|baloch|"
        r"sindhis?|punjabis?|kashmiris?|refugees?|migrants?|immigrants?)\b[^.!?]{0,30}\b"
        r"(animals?|insects?|cockroaches?|vermin|snakes?|dogs?|dogs|monkeys?)\b"
    ),
    re.compile(
        r"(?i)\b(animals?|insects?|cockroaches?|vermin)\b[^.!?]{0,30}\b"
        r"(muslims?|hindus?|sikhs?|christians?|ahmadis?|shias?|sunnis?|pashtuns?|baloch|refugees?|migrants?)\b"
    ),
    re.compile(
        r"(?i)\ball\s+(muslims?|hindus?|sikhs?|christians?|ahmadis?|refugees?|migrants?)\s+are\s+"
        r"(terrorists?|criminals?|enem(y|ies)|dogs|animals?)"
    ),
]


def _at_to_a(text: str) -> str:
    """Map an intra-word ``@`` to ``a`` without changing the string length."""
    return re.sub(r"(?<=[0-9A-Za-z\u0600-\u06ff])@(?=[0-9A-Za-z\u0600-\u06ff])", "a", text)


def _strip_intra_word(text: str) -> str:
    """Remove separator characters inserted inside a word ("f*ck", "h.a.r.a.m.i")."""
    return re.sub(
        r"(?<=[0-9A-Za-z\u0600-\u06ff])[*\-_.#+]+(?=[0-9A-Za-z\u0600-\u06ff])",
        "",
        text,
    )


@dataclass(frozen=True)
class ModerationResult:
    language: str
    classification: str
    reason: str
    profanity: str
    score: int
    quoted_profanity: bool = False
    routed_to_review: bool = False
    flags: list[str] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)


def _iter_profanity_hits(text: str):
    """Yield (span, severity, category, quoted) for every profanity match.

    Matching runs over three derived forms so obfuscation, misspelling and
    repeated characters are caught without a huge third-party word list:
    cleaned tokens, leet-mapped tokens and vowel-stripped skeletons.
    """
    if not text:
        return
    seen: set[tuple[int, int]] = set()
    # Compact intra-word obfuscation ("f*ck", "h.a.r.a.m.i") so the separator
    # characters cannot hide a term from the token dictionaries. An "@" is
    # mapped to "a" first (so "h@r@mi" becomes "harami") because replacing it
    # would lose a real letter from the word.
    scan_text = _at_to_a(text)
    scan_text = _strip_intra_word(scan_text)
    # Pass 1: word tokens (Latin, Roman Urdu, obfuscated).
    for match in re.finditer(r"[\w'\u0600-\u06ff]{2,}", scan_text, re.UNICODE):
        token = match.group(0)
        cleaned = re.sub(r"[^0-9a-z\u0600-\u06ff]", "", token.casefold())
        if not cleaned:
            continue
        entry = _PROFANITY_FORMS.get(cleaned) or _PROFANITY_URDU.get(cleaned)
        kind = "exact"
        if entry is None:
            leet = "".join(_LEET_MAP.get(c, c) for c in cleaned)
            entry = _PROFANITY_FORMS.get(leet)
            kind = "obfuscated"
            if entry is None:
                for variant in {re.sub(r"(.)\1+", r"\1\1", leet), re.sub(r"(.)\1+", r"\1", leet)}:
                    entry = _PROFANITY_FORMS.get(variant)
                    kind = "obfuscated"
                    if entry:
                        break
            if entry is None:
                skeleton = _skeleton(leet)
                # Skeleton matches need 4+ characters to stay safe against
                # false positives ("gl" for "gali" is far too short).
                if len(skeleton) >= 4:
                    entry = _PROFANITY_FORMS.get(skeleton)
                    kind = "fuzzy"
        if entry is None:
            continue
        severity, category = entry
        if kind == "fuzzy" and severity in {"mild", "strong"}:
            severity = "uncertain"
        span = match.span()
        if span in seen:
            continue
        seen.add(span)
        prefix = scan_text[: span[0]]
        quoted = prefix.count('"') % 2 == 1 or prefix.count("\u201d") % 2 == 1
        yield span, severity, category, quoted
    # Pass 2: multi-word phrase profanity.
    for pattern in _PROFANITY_PHRASES:
        for match in pattern.finditer(scan_text):
            span = match.span()
            if any(span[0] < other[1] and other[0] < span[1] for other in seen):
                continue
            seen.add(span)
            prefix = scan_text[: span[0]]
            quoted = prefix.count('"') % 2 == 1 or prefix.count("\u201d") % 2 == 1
            yield span, "targeted_abuse", "sexual_or_explicit", quoted
    # Pass 3: threat / hate patterns (span-level, quoted-aware).
    for pattern, category in [(p, "threat") for p in _THREAT_PATTERNS] + [
        (p, "hate_or_dehumanizing") for p in _HATE_PATTERNS
    ]:
        for match in pattern.finditer(scan_text):
            span = match.span()
            if any(span[0] < other[1] and other[0] < span[1] for other in seen):
                continue  # already counted as a direct term
            seen.add(span)
            prefix = scan_text[: span[0]]
            quoted = prefix.count('"') % 2 == 1 or prefix.count("\u201d") % 2 == 1
            severity = "threat" if category == "threat" else "hate_or_dehumanizing"
            yield span, severity, category, quoted


def profanity_details(text: str) -> dict:
    """Full moderation result for a piece of text (draft output or source)."""
    hits = list(_iter_profanity_hits(text or ""))
    if not hits:
        return {"level": "none", "tokens": [], "quoted": False, "needs_review": False}
    levels = [severity for _, severity, _, _ in hits]
    level = max(levels, key=lambda value: _PROFANITY_ORDER.index(value))
    tokens = [(text or "")[span[0] : span[1]] for span, _, _, _ in hits]
    needs_review = any(severity in {"targeted_abuse", "threat", "hate_or_dehumanizing"} for _, severity, _, _ in hits)
    return {
        "level": level,
        "tokens": tokens,
        "quoted": any(quoted for *_, quoted in hits),
        "needs_review": needs_review,
    }


def detect_profanity(text: str) -> str:
    return profanity_details(text)["level"]


def redact_profanity(text: str, *, keep_quotes: bool = True) -> tuple[str, list[str]]:
    """Remove gratuitous profanity from drafted prose.

    Tokens inside double quotes are editorially labelled quotes; with
    ``keep_quotes`` (the default for body text) they are left for the editor.
    Headlines and decks pass ``keep_quotes=False``: a newspaper headline never
    prints profanity, quoted or not.
    """
    if not text:
        return text, []
    hits_all = list(_iter_profanity_hits(text))
    if not hits_all:
        return text, []
    if re.sub(r"(?<=[0-9A-Za-z\u0600-\u06ff])[*\-_.#+]+(?=[0-9A-Za-z\u0600-\u06ff])", "", text) != text:
        # Obfuscated spellings: the spans refer to a compacted copy, so we
        # cannot safely splice the original. Report without altering text;
        # an editor handles obfuscated wording during review.
        return text, [(text or "")[span[0] : span[1]] for span, _, _, _ in hits_all]
    hits = [hit for hit in hits_all if not (keep_quotes and hit[3])]
    if not hits:
        return text, []
    removed: list[str] = []
    out = []
    cursor = 0
    for span, severity, _, _ in hits:
        if severity == "mild":
            continue
        out.append(text[cursor : span[0]])
        out.append("\u2026")
        removed.append(text[span[0] : span[1]])
        cursor = span[1]
    out.append(text[cursor:])
    return "".join(out), removed


# ---------------------------------------------------------------------------
# Attribution audit
# ---------------------------------------------------------------------------


def _pub_flexible(name: str) -> str:
    """A publication name regex that ignores spacing/hyphen differences, so
    both 'The Pakistan News' and 'ThePakistanNews' are recognised."""
    parts = [re.escape(part) for part in re.split(r"[\s_\-]+", (name or "").strip()) if part]
    return r"[\s_\-]*".join(parts)


def attribution_issues(text: str, publication: str) -> list[str]:
    """Flag person-like references to the publication or invented staff.

    The channel/publication may be cited as an institutional source ("a post
    published on X said"), but it must never be written as a reporter,
    correspondent or eyewitness, and no staff writer may be invented.
    """
    if not text:
        return []
    issues: list[str] = []
    pub = _pub_flexible(publication)
    patterns: list[tuple[str, str]] = [
        (
            rf"\b(?:reporter|correspondent|journalist|eyewitness|columnist|anchor)\b[\s\S]{{0,40}}?\b(?:for\s+)?(?:{pub})\b",
            "publication named as an individual reporter/correspondent",
        ),
        (
            rf"\b(?:{pub})\b[\s\S]{{0,30}}?\b(?:witnessed|observed first-hand|was present at|saw the attack|saw the blast)\b",
            "publication described as an eyewitness",
        ),
        (
            r"\b(?:{pub})\b\s+reported\s+from\s+the\s+scene\b".format(pub=pub),
            "publication described as reporting live from the scene",
        ),
        (
            rf"\b(?:{pub})\b\s+(?:correspondent|reporter)\b",
            "publication used as the employer of an unnamed correspondent",
        ),
        (
            r"\b(our|a)\s+(?:staff\s+)?(?:correspondent|reporter|journalist|eyewitness|correspondents)\b",
            "invented staff correspondent/reporter reference",
        ),
        (
            rf"\baccording\s+to\s+(?:reporter|correspondent|journalist)\s+(?:{pub})\b",
            "publication attributed as a individual reporter",
        ),
    ]
    for pattern, message in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            issues.append(message)
    return issues


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_UNSUPPORTED_MEDIA_KINDS = {"document", "audio", "sticker", "voice", "animation_file"}


def _media_kinds(message: NormalizedMessage) -> list[str]:
    kinds = []
    for item in message.media or []:
        if isinstance(item, dict):
            kinds.append(str(item.get("kind") or "").casefold())
        else:
            kinds.append(str(item).casefold())
    return kinds


def _signal_counts(normalized: str) -> tuple[int, int]:
    """Return (all strong signals, word-level event signals).

    The first count includes bare digits, which prove that something specific
    was written. The second excludes digits: an event word is required before a
    greeting, slogan or promotion can be treated as a report.
    """
    total = len(_STRONG_SIGNAL.findall(normalized))
    events = len(_EVENT_SIGNAL.findall(normalized))
    for token in normalized.split():
        if token in _ROMAN_STRONG:
            total += 1
            events += 1
    return total, events


_NUMBER_SIGNAL = re.compile(r"(?<![\w.])\d{1,4}(?:[.,]\d+)?(?![\w.])")


def _unique_matches(pattern: re.Pattern[str], text: str, *, limit: int = 8) -> list[str]:
    """First distinct matched forms, lower-cased and bounded.

    Signals are persisted and shown to editors as evidence for a
    classification decision; they never replace the original post text.
    """
    found: list[str] = []
    for match in pattern.finditer(text):
        value = re.sub(r"\s+", " ", match.group(0)).strip().casefold()
        if value and value not in found:
            found.append(value)
        if len(found) >= limit:
            break
    return found


def _classification_signals(
    *,
    text: str,
    normalized: str,
    language: str,
    profanity: str,
    quoted: bool,
    media_kinds: list[str],
    deleted: bool,
    forwarded: bool,
    injected: bool,
) -> list[dict]:
    """Explain a deterministic classification with bounded matched signals."""
    signals: list[dict] = []

    def add(kind: str, values: list[str]) -> None:
        values = [value for value in values if value]
        if values:
            signals.append({"kind": kind, "values": values[:8]})

    add("event_terms", _unique_matches(_EVENT_SIGNAL, normalized))
    roman = [token for token in normalized.split() if token in _ROMAN_STRONG]
    add("roman_urdu_terms", list(dict.fromkeys(roman)))
    add("numbers", _unique_matches(_NUMBER_SIGNAL, normalized))
    add("weak_terms", _unique_matches(_WEAK_SIGNAL, text))
    add("media", sorted(set(media_kinds)))
    add("urls", _unique_matches(_URL, text, limit=3))
    if _GREETING_SIGNAL.match(text) or _GREETING_URDU.match(text) or _GREETING_PREFIX.match(text):
        add("greeting", ["greeting_or_condolence"])
    if _SLOGAN_SIGNAL.match(text):
        add("slogan", ["generic_slogan"])
    add("promotion", _unique_matches(_PROMOTION, normalized))
    add("engagement_bait", _unique_matches(_ENGAGEMENT_BAIT, normalized))
    if _EMOJI.search(text):
        add("emoji", ["emoji_present"])
    if injected:
        add("injection", ["instruction_injection_neutralised"])
    if language != "en":
        add("language", [language])
    if profanity != "none":
        # Never persist the matched term itself; the severity is enough for an
        # editor and keeps abusive wording out of secondary logs.
        add("profanity", [profanity])
    if quoted:
        add("quoted_profanity", ["quoted"])
    if forwarded:
        add("forwarded", ["telegram_forward"])
    if deleted:
        add("deleted", ["deleted_at_source"])
    return signals



def classify_message(message: NormalizedMessage, *, rights_ok: bool) -> ModerationResult:
    """Deterministically classify one source post. Untrusted input in, flags out."""
    from .claims import contains_injection  # local import avoids any import cycle

    text = (message.text or "").strip()
    normalized = normalize_for_analysis(strip_urls(text))
    language = detect_language_family(text)
    details = profanity_details(text)
    profanity = details["level"]
    flags: list[str] = []
    if details["needs_review"]:
        flags.append("moderation_review")
    if details["quoted"]:
        flags.append("quoted_profanity")

    def result(classification: str, reason: str, *, score: int = 0) -> ModerationResult:
        return ModerationResult(
            language=language,
            classification=classification,
            reason=reason,
            profanity=profanity,
            score=score,
            quoted_profanity=details["quoted"],
            routed_to_review="moderation_review" in flags,
            flags=flags,
            signals=signals,
        )

    kinds = _media_kinds(message)
    strong, events = _signal_counts(normalized)
    # Institutions alone are not a report. Remove actor-only words before the
    # abuse gate so "fuck the police" cannot borrow "police" as an event.
    report_events = _signal_counts(_ACTOR_ONLY_SIGNAL.sub(" ", normalized))[1]
    weak = len(_WEAK_SIGNAL.findall(text))
    injected = contains_injection(text)
    has_media = bool(kinds)
    signals = _classification_signals(
        text=text,
        normalized=normalized,
        language=language,
        profanity=profanity,
        quoted=details["quoted"],
        media_kinds=kinds,
        deleted=message.deleted,
        forwarded=bool(message.forward_origin),
        injected=injected,
    )

    # 1-4: hard gates first.
    if message.deleted:
        return result("rights_blocked", "deleted")
    if message.forward_origin:
        return result("duplicate", "telegram_forward")
    if not text and not kinds:
        return result("empty", "no_text_or_media")
    if not rights_ok:
        return result("rights_blocked", "rights_unconfirmed")

    # 5: link-only reposts with no commentary of their own.
    if not normalized and not kinds and _URL.search(text):
        return result("context_only", "link_only_no_commentary")

    # 6: reaction/emoji-only content carries no editorial information.
    if not normalized and not kinds:
        return result(
            "context_only",
            "emoji_or_reaction_only" if _EMOJI.search(text) else "symbols_only",
        )

    # Unsupported media with no usable caption cannot enter layout.
    if not normalized and kinds and all(kind in _UNSUPPORTED_MEDIA_KINDS for kind in kinds):
        return result("unsupported_media_only", "unsupported_media_no_caption")

    # 7: profanity/abuse with nothing reportable behind it. A photo does not
    # turn an abusive caption into a report; route it to a human instead.
    if profanity in {"strong", "sexual_or_explicit", "targeted_abuse", "threat", "hate_or_dehumanizing"} and report_events == 0:
        if injected:
            return result("low_information", "instruction_injection_without_news")
        return result("profanity_only", "abuse_without_reportable_content")

    # 8: greetings, prayers, congratulations and condolences with no event.
    if events == 0 and not has_media and (
        _GREETING_SIGNAL.match(text)
        or _GREETING_URDU.match(text)
        or _GREETING_PREFIX.match(text)
    ):
        return result("context_only", "greeting_or_condolence_no_event")

    # 9: generic slogans.
    if events == 0 and not has_media and _SLOGAN_SIGNAL.match(text):
        return result("context_only", "generic_slogan")

    # 10: promotions, subscription appeals and engagement bait. A promotional
    # caption riding on an image is still a promotion, not a photo report. A
    # bare digit (discount, year, prize amount) is not an event word.
    if events == 0 and (_PROMOTION.search(normalized) or _ENGAGEMENT_BAIT.search(normalized)):
        if injected:
            return result("low_information", "instruction_injection_without_news")
        return result("promotion", "promotion_or_channel_call")

    # 11: prompt injection is neutralised; alone it carries no news.
    if injected and events == 0 and not has_media:
        return result("low_information", "instruction_injection_without_news")

    # 12: unrecognised scripts need a human language pass, but are retained.
    if language == "other":
        return result("language_needs_review", "unrecognised_script")

    # 13: media posts stay eligible — the album/caption path is how several
    # real channels publish. Weak-only text plus media is still a photo report.
    if has_media:
        if events:
            return result("editorially_relevant", "news_signal_with_media", score=strong)
        return result("editorially_relevant", "media_source", score=max(strong, 1))

    # 14: "breaking"/"update" with no information must not pass.
    if weak and events == 0:
        return result("low_information", "breaking_or_update_without_information")

    # 15: real signals, however short or informal.
    if events:
        reason = "news_signal"
        if len(normalized.split()) <= 12:
            reason = "short_alert_retained"
        if language in {"ur", "roman_ur", "mixed_ur_en"}:
            reason = f"{reason}_urdu"
        return result("editorially_relevant", reason, score=strong)

    # 16: digits without any event word ("5pm", "2026", "50%") are not a report.
    if strong:
        return result("low_information", "number_without_event_context")

    # 17: nothing deterministic recognised; keep for low-score review rather
    # than delete, but exclude from drafting with an explicit reason.
    return result("low_information", "no_editorial_signal")


def exclusion_for(
    result: ModerationResult,
    *,
    deleted: bool = False,
    service_event: bool = False,
) -> str | None:
    """Map a classification to the legacy exclusion-reason vocabulary.

    Returns ``None`` when the post stays included in story drafting.
    ``language_needs_review`` posts remain included: an unfamiliar script is
    not grounds for silently dropping a potentially important post.
    """
    if deleted:
        return "deleted"
    if service_event:
        return "service_event"
    if result.classification in {"editorially_relevant", "language_needs_review"}:
        return None
    if result.classification == "duplicate":
        return "repeated_copy" if result.reason == "repeated_copy" else "telegram_forward"
    if result.classification in {"context_only", "low_information", "profanity_only", "unsupported_media_only"}:
        # Keep the specific classifier reason instead of collapsing every
        # contextual or low-information post into one opaque label.
        return result.reason
    return {
        "promotion": "promotion_only",
        "rights_blocked": "rights_unconfirmed",
    }.get(result.classification, result.classification)
