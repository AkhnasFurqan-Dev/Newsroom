from __future__ import annotations

import json
from pathlib import Path

from ..config import settings

MODEL_MANIFEST_PATH = Path(__file__).with_name("model_manifest.json")
MODEL_MANIFEST = json.loads(MODEL_MANIFEST_PATH.read_text(encoding="utf-8"))
MODEL_PROFILES = {profile["id"]: profile for profile in MODEL_MANIFEST["profiles"]}


def profile_for_model(model: str) -> dict | None:
    return next(
        (profile for profile in MODEL_PROFILES.values() if profile["tag"] == model),
        None,
    )


def profile_for_id(profile_id: str) -> dict:
    try:
        return MODEL_PROFILES[profile_id]
    except KeyError as exc:
        raise ValueError("Select a supported Qwen3.5 model profile.") from exc


def apply_saved_model_selection(db) -> None:
    from ..models import AppSetting

    model_setting = db.get(AppSetting, "ai.model_tag")
    profile_setting = db.get(AppSetting, "ai.model_profile")
    if model_setting is not None and model_setting.value.strip():
        settings.ollama_model = model_setting.value.strip()
    if profile_setting is not None and profile_setting.value in MODEL_PROFILES:
        settings.ollama_model_profile = profile_setting.value
