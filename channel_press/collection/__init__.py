"""Cutoff-to-cutoff collection windows and durable retrieval state."""

from .service import (
    CollectionError,
    CollectionStats,
    FixtureWindowSource,
    LiveTelegramSource,
    RightsGateError,
    collect_window,
    commit_edition_window,
    preview_window,
    window_summary,
)
from .window import EditionWindow, window_for_cutoff, window_for_schedule

__all__ = [
    "CollectionError",
    "CollectionStats",
    "EditionWindow",
    "FixtureWindowSource",
    "LiveTelegramSource",
    "RightsGateError",
    "collect_window",
    "commit_edition_window",
    "preview_window",
    "window_for_cutoff",
    "window_for_schedule",
    "window_summary",
]
