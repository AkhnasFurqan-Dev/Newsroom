from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from ..config import settings
from ..logging_setup import get_logger
from .model_profiles import MODEL_MANIFEST, profile_for_id

logger = get_logger("model_manager")
_STATE_LOCK = threading.RLock()
_INSTALL_STATE: dict[str, Any] = {
    "state": "idle",
    "profile_id": "",
    "tag": "",
    "detail": "No model download is running.",
    "completed": 0,
    "total": 0,
    "percent": 0,
    "updated_at": "",
}
_CANCEL = threading.Event()
_THREAD: threading.Thread | None = None
_SMOKE_LOCK = threading.RLock()
_SMOKE_STATE: dict[str, Any] = {
    "state": "idle",
    "profile_id": "",
    "tag": "",
    "detail": "No local model smoke test has run.",
}
_SMOKE_THREAD: threading.Thread | None = None


def install_status() -> dict[str, Any]:
    with _STATE_LOCK:
        return dict(_INSTALL_STATE)


def _update(**values: Any) -> None:
    with _STATE_LOCK:
        _INSTALL_STATE.update(values, updated_at=datetime.now(UTC).isoformat())


def _registry_manifest(profile: dict) -> tuple[dict, str]:
    tag = profile["tag"].split(":", 1)[1]
    url = f"{MODEL_MANIFEST['registry']}/manifests/{quote(tag, safe='')}"
    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
        headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
    ) as client:
        response = client.get(url)
        response.raise_for_status()
        manifest_bytes = response.content
        manifest = response.json()
        digest = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
        if digest != profile["registry_manifest_digest"]:
            raise ValueError(
                "The upstream model tag no longer matches the reviewed release digest."
            )
        expected_layers = profile["layers"]
        actual_layers: dict[str, str] = {}
        for layer in manifest.get("layers", []):
            media_type = str(layer.get("mediaType", ""))
            if media_type.endswith(".projector"):
                name = "projector"
            elif media_type.endswith(".model"):
                name = "model"
            elif media_type.endswith(".template"):
                name = "template"
            elif media_type.endswith(".license"):
                name = "license"
            elif media_type.endswith(".params"):
                name = "parameters"
            else:
                raise ValueError("The upstream model contains an unreviewed layer.")
            actual_layers[name] = str(layer.get("digest", ""))
        if actual_layers != expected_layers:
            raise ValueError("The upstream model layer hashes do not match the reviewed manifest.")
        if sum(int(layer["size"]) for layer in manifest.get("layers", [])) != int(
            profile["download_bytes"]
        ):
            raise ValueError("The upstream model size differs from the reviewed package size.")
        license_layer = next(
            layer for layer in manifest["layers"] if layer["mediaType"].endswith(".license")
        )
        license_response = client.get(
            f"{MODEL_MANIFEST['registry']}/blobs/{license_layer['digest']}"
        )
        license_response.raise_for_status()
        if b"Apache License" not in license_response.content[:200]:
            raise ValueError("The model's embedded license is not the reviewed Apache-2.0 text.")
        if (
            hashlib.sha256(license_response.content).hexdigest()
            != license_layer["digest"].split(":", 1)[1]
        ):
            raise ValueError("The model license layer failed its SHA-256 check.")
        return manifest, digest


def _local_ollama_url() -> str:
    parts = urlsplit(settings.ollama_base_url)
    if (
        parts.scheme != "http"
        or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parts.username
        or parts.password
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Model downloads require the local Ollama service on this computer.")
    return settings.ollama_base_url.rstrip("/")


def installed_models() -> list[dict[str, Any]]:
    base_url = _local_ollama_url()
    with httpx.Client(timeout=5) as client:
        response = client.get(f"{base_url}/api/tags")
        response.raise_for_status()
        payload = response.json()
    return [
        {
            "name": str(item.get("name") or ""),
            "digest": str(item.get("digest") or ""),
            "size": int(item.get("size") or 0),
        }
        for item in payload.get("models", [])
        if isinstance(item, dict)
    ]


def installed_profile(profile: dict) -> dict[str, Any] | None:
    return next(
        (
            model
            for model in installed_models()
            if model["name"] == profile["tag"] and model["size"] == int(profile["download_bytes"])
        ),
        None,
    )


def start_install(profile_id: str) -> dict[str, Any]:
    global _THREAD
    profile = profile_for_id(profile_id)
    _local_ollama_url()
    with _STATE_LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return dict(_INSTALL_STATE)
        _CANCEL.clear()
        _INSTALL_STATE.update(
            state="verifying",
            profile_id=profile_id,
            tag=profile["tag"],
            detail="Checking the pinned model manifest and license.",
            completed=0,
            total=int(profile["download_bytes"]),
            percent=0,
            updated_at=datetime.now(UTC).isoformat(),
        )
        _THREAD = threading.Thread(
            target=_install,
            args=(profile,),
            name="channel-press-model-download",
            daemon=True,
        )
        _THREAD.start()
        return dict(_INSTALL_STATE)


def cancel_install() -> None:
    _CANCEL.set()
    if _THREAD is not None and _THREAD.is_alive():
        _update(state="canceling", detail="Stopping the model transfer safely.")


def cancel_and_wait(timeout: float = 30) -> bool:
    cancel_install()
    thread = _THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
    return thread is None or not thread.is_alive()


def wait_for_smoke_test(timeout: float = 90) -> bool:
    thread = _SMOKE_THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
    return thread is None or not thread.is_alive()


def smoke_status() -> dict[str, Any]:
    with _SMOKE_LOCK:
        return dict(_SMOKE_STATE)


def model_smoke_record(db, model: str, digest: str = "") -> dict[str, Any] | None:
    from ..models import AppSetting

    setting = db.get(AppSetting, f"ai.smoke.{model}")
    if setting is None:
        return None
    try:
        record = json.loads(setting.value)
    except (TypeError, json.JSONDecodeError):
        return None
    if record.get("model") != model or (digest and record.get("model_digest") != digest):
        return None
    return record if record.get("status") == "passed" else None


def start_smoke_test(profile_id: str) -> dict[str, Any]:
    global _SMOKE_THREAD
    profile = profile_for_id(profile_id)
    with _SMOKE_LOCK:
        if _SMOKE_THREAD is not None and _SMOKE_THREAD.is_alive():
            return dict(_SMOKE_STATE)
        _SMOKE_STATE.update(
            state="testing",
            profile_id=profile_id,
            tag=profile["tag"],
            detail="Running a short synthetic, source-grounded draft and schema check.",
        )
        _SMOKE_THREAD = threading.Thread(
            target=_run_smoke_test,
            args=(profile,),
            name="channel-press-model-smoke-test",
            daemon=True,
        )
        _SMOKE_THREAD.start()
        return dict(_SMOKE_STATE)


def _run_smoke_test(profile: dict) -> None:
    from types import SimpleNamespace

    from ..models import AppSetting
    from ..timeutil import utcnow
    from .providers import OllamaDraftProvider, SourceEvidence, StoryBundle

    provider = OllamaDraftProvider(model=profile["tag"])
    try:
        available, reason = provider.available()
        if not available:
            raise RuntimeError(reason)
        source_text = (
            "Synthetic fixture FX-01: the fictional Example Ward recorded exactly "
            "three sample buses changing routes on 30 October 2026. The fixture "
            "does not state why the routes changed."
        )
        bundle = StoryBundle(
            story=SimpleNamespace(
                canonical_title="Synthetic route-change smoke test",
                genre="news",
            ),
            sources=[
                SourceEvidence(
                    post_id=1,
                    message_id=1,
                    posted_at="2026-10-30T00:00:00+00:00",
                    text=source_text,
                    source_url="",
                    source_label="Synthetic fixture FX-01",
                )
            ],
            claims=[],
            timeline=[],
        )
        result = provider.draft(bundle)
        if result.provider != "ollama" or not result.title.strip() or not result.body:
            raise RuntimeError("The model failed the source-grounded structured draft check.")
        if result.model != profile["tag"] or not result.model_digest:
            raise RuntimeError("The local runtime did not report the expected model digest.")
        record = {
            "status": "passed",
            "model": profile["tag"],
            "model_digest": result.model_digest,
            "registry_manifest_digest": profile["registry_manifest_digest"],
            "provider_version": result.provider_version,
            "prompt_version": result.prompt_version,
            "generation_settings": result.generation_settings,
            "tested_at": utcnow().isoformat(),
            "latency_ms": result.latency_ms,
            "synthetic_fixture": "FX-01",
        }
        from ..db import SessionLocal

        with SessionLocal() as db:
            setting = db.get(AppSetting, f"ai.smoke.{profile['tag']}")
            encoded = json.dumps(record, ensure_ascii=False)
            if setting is None:
                db.add(AppSetting(key=f"ai.smoke.{profile['tag']}", value=encoded))
            else:
                setting.value = encoded
                setting.updated_at = utcnow()
            db.commit()
        with _SMOKE_LOCK:
            _SMOKE_STATE.update(
                state="passed",
                detail="Synthetic draft passed the model response schema check.",
                model_digest=result.model_digest,
                provider_version=result.provider_version,
                prompt_version=result.prompt_version,
                latency_ms=result.latency_ms,
            )
    except Exception as exc:
        logger.exception("model_smoke_test_failed", extra={"kind": "model_smoke"})
        with _SMOKE_LOCK:
            _SMOKE_STATE.update(
                state="failed",
                detail=f"Local model test failed ({type(exc).__name__}). Inspect Diagnostics.",
            )
    finally:
        provider.unload()


def _install(profile: dict) -> None:
    try:
        _, pinned_digest = _registry_manifest(profile)
        if _CANCEL.is_set():
            _update(state="canceled", detail="Model download canceled before transfer.")
            return
        _update(state="downloading", detail="Connecting to the local Ollama model service.")
        base_url = _local_ollama_url()
        with (
            httpx.Client(timeout=httpx.Timeout(connect=10, read=10, write=30, pool=10)) as client,
            client.stream(
                "POST",
                f"{base_url}/api/pull",
                json={"name": profile["tag"], "stream": True},
            ) as response,
        ):
            response.raise_for_status()
            for line in response.iter_lines():
                if _CANCEL.is_set():
                    _update(
                        state="canceled",
                        detail="Model download canceled. The runtime may retain verified layers for a retry.",
                    )
                    return
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        "The local model service returned an invalid progress event."
                    ) from exc
                if item.get("error"):
                    raise RuntimeError("The local model service reported a download failure.")
                completed = int(item.get("completed") or 0)
                total = int(item.get("total") or profile["download_bytes"])
                percent = min(100, int(completed * 100 / max(total, 1)))
                _update(
                    state="downloading",
                    detail=str(item.get("status") or "Downloading model layers."),
                    completed=completed,
                    total=total,
                    percent=percent,
                )
        if _CANCEL.is_set():
            _update(state="canceled", detail="Model download canceled.")
            return
        _, current_digest = _registry_manifest(profile)
        if current_digest != pinned_digest:
            raise RuntimeError("The upstream model tag changed during installation.")
        local = installed_profile(profile)
        if local is None:
            raise RuntimeError("Ollama did not confirm the exact pinned model after download.")
        _update(
            state="complete",
            detail="The pinned model is installed and its local digest was recorded.",
            completed=int(profile["download_bytes"]),
            total=int(profile["download_bytes"]),
            percent=100,
            registry_digest=pinned_digest,
            local_digest=local["digest"],
        )
    except (httpx.HTTPError, ValueError, RuntimeError) as exc:
        logger.exception("model_install_failed", extra={"kind": "model_download"})
        _update(
            state="failed",
            detail=f"Model setup failed ({type(exc).__name__}). Retry or inspect Diagnostics.",
            error_type=type(exc).__name__,
        )
    except Exception:
        logger.exception("unexpected_model_install_failure", extra={"kind": "model_download"})
        _update(
            state="failed",
            detail="Model setup failed unexpectedly. Inspect the private diagnostics log.",
            error_type="UnexpectedError",
        )
