from __future__ import annotations

SECTIONS = [
    ("front", "Front Page"),
    ("national", "National & Internal"),
    ("regional", "Regional"),
    ("international", "International & Geopolitics"),
    ("defence", "Defence & Security"),
    ("conflict", "Conflict & War"),
    ("analysis", "Analysis"),
    ("opinion", "Opinion & Editorial"),
    ("briefs", "Briefs"),
]
SECTION_LABELS = dict(SECTIONS)
SECTION_ORDER = [key for key, _ in SECTIONS if key != "front"]

GENRES = ["news", "analysis", "opinion", "editor_note", "brief"]

ROLE_RANK = {"viewer": 10, "operator": 20, "editor": 30, "owner": 40}

JOB_ACTIVE_STATUSES = {
    "scheduled",
    "source_validation",
    "fetching",
    "normalization",
    "clustering",
    "drafting",
    "auditing",
    "media",
    "layout",
    "quality_check",
    "publishing",
}
JOB_TERMINAL = {"published", "failed_terminal", "canceled"}
JOB_PAUSED = {"awaiting_review", "ready_to_publish", "resumable", "failed_retryable"}

EXCLUSION_REASONS = {
    "telegram_forward": "Telegram-native forwarded post",
    "promotion_only": "Promotion/channel-link-only post",
    "empty": "No text or media",
    "service_event": "Service event without news content",
    "rights_unconfirmed": "No active content-rights grant",
    "deleted": "Deleted or withdrawn at source",
    "duplicate": "Duplicate of an ingested source message",
}

RISK_TERMS = {
    "casualties": ["killed", "dead", "death toll", "injured", "wounded", "wounding", "casualties"],
    "allegation": ["alleged", "accused", "corruption", "bribery", "abuse"],
    "operational": ["coordinates", "grid reference", "troop movement", "convoy route", "tactical position"],
    "minors": ["child", "children", "minor", "school attack"],
    "graphic": ["beheaded", "execution video", "mutilated", "corpse"],
    "sensitive_personal": ["home address", "phone number", "identity card", "passport number"],
}

STOCK_PHRASES = [
    "in a significant development",
    "this underscores",
    "the unfolding situation",
    "sent shockwaves",
    "as tensions continue to rise",
    "it remains to be seen",
    "a testament to",
    "a stark reminder",
    "amid mounting concerns",
    "underscoring",
    "amid growing tensions",
    "in a move that",
]

SECTION_KEYWORDS = {
    "conflict": [
        "attack", "strike", "militant", "militants", "bomb", "blast", "gunmen", "troops",
        "airstrike", "ceasefire", "casualties", "killed", "wounded", "offensive", "clashes",
    ],
    "defence": [
        "army", "military", "air force", "navy", "defence", "defense", "security forces",
        "interceptors", "drone", "missile test", "garrison", "paramilitary", "rangers",
    ],
    "international": [
        "united nations", "washington", "beijing", "delhi", "tehran", "kabul", "treaty",
        "sanctions", "diplomat", "foreign minister", "border talks", "summit", "embassy",
    ],
    "regional": [
        "province", "district", "balochistan", "sindh", "punjab", "khyber", "gilgit",
        "kashmir", "quetta", "peshawar", "lahore", "karachi", "gilgit-baltistan",
    ],
    "analysis": ["analysis", "explainer", "why ", "what it means", "outlook"],
    "opinion": ["opinion", "editorial", "commentary"],
    "national": [
        "government", "parliament", "cabinet", "prime minister", "president", "court",
        "policy", "ministry", "economy", "inflation", "budget", "election",
    ],
}

# Source categories are labels, not numeric truth scores.
SOURCE_CATEGORIES = [
    "channel_original",
    "primary_official",
    "independent_reporting",
    "direct_witness",
    "unverified_social",
    "editor_supplied_context",
]
