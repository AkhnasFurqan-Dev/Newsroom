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
    "repeated_copy": "Repeated copy of an already accepted post",
    "context_only": "Context only: no reportable development",
    "greeting_or_condolence_no_event": "Greeting, prayer or condolence with no event detail",
    "emoji_or_reaction_only": "Emoji or reaction only",
    "symbols_only": "Symbols or digits with no words",
    "link_only_no_commentary": "Link without meaningful source commentary",
    "generic_slogan": "Generic slogan with no reportable content",
    "profanity_only": "Profanity or abuse without reportable content",
    "abuse_without_reportable_content": "Abuse or threats with no reportable content",
    "unsupported_media_only": "Unsupported media without a usable caption",
    "unsupported_media_no_caption": "Unsupported media without a usable caption",
    "low_information": "No recognised editorial signal",
    "no_editorial_signal": "No recognised editorial signal",
    "breaking_or_update_without_information": "\"Breaking\"/\"update\" with no information",
    "number_without_event_context": "Numbers without a reportable event",
    "instruction_injection_without_news": "Embedded instructions with no news content",
    "language_needs_review": "Unrecognised script: kept for language review",
    "unrecognised_script": "Unrecognised script: kept for language review",
    "editor_excluded": "Excluded by editor decision",
    "editor_context_only": "Kept as context only by editor decision",
    "editor_promotion": "Marked promotional by editor decision",
    "editor_profanity": "Marked profanity/abuse by editor decision",
}

EDITOR_OVERRIDE_CHOICES = {
    "include": "Include in story processing",
    "exclude": "Exclude from story processing",
    "context_only": "Include as context only",
    "promotion": "Mark as promotional",
    "profanity": "Mark as profanity/abuse",
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
