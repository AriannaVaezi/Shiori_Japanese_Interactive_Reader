"""Application settings and LLM provider testing routes."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app import config, db, llamacpp_manager, llm

router = APIRouter(prefix="/api", tags=["settings"])

_LLM_LIFECYCLE_KEYS = {
    "llm_provider",
    "llamacpp_url",
    "llamacpp_model",
    "llamacpp_api_key",
    "llamacpp_model_path",
    "llamacpp_autostart",
}


class SettingsUpdateRequest(BaseModel):
    llm_provider: str | None = None
    llamacpp_url: str | None = None
    llamacpp_model: str | None = None
    llamacpp_api_key: str | None = None
    clear_llamacpp_key: bool = False
    llamacpp_model_path: str | None = None
    llamacpp_autostart: str | None = None
    ollama_url: str | None = None
    ollama_model: str | None = None
    gemini_api_key: str | None = None
    clear_gemini_key: bool = False
    gemini_model: str | None = None
    reading_mode: str | None = None
    furigana_mode: str | None = None
    theme: str | None = None
    font_size: str | None = None
    quiz_timer_minutes: str | None = None
    manga_layout: str | None = None
    manga_spread_offset: str | None = None
    manga_ocr_overlay: str | None = None
    hide_annotations: str | None = None


class LLMTestRequest(BaseModel):
    llm_provider: str | None = None
    llamacpp_url: str | None = None
    llamacpp_model: str | None = None
    llamacpp_api_key: str | None = None
    ollama_url: str | None = None
    ollama_model: str | None = None
    gemini_api_key: str | None = None
    gemini_model: str | None = None


def _mask_secret(secret: str) -> str:
    clean = secret.strip()
    if not clean:
        return ""
    if len(clean) <= 8:
        return "••••"
    return f"{clean[:4]}••••{clean[-4:]}"


def _public_settings(raw: dict[str, str]) -> dict[str, Any]:
    saved_key = raw.get("gemini_api_key", "").strip()
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    effective_key = saved_key or env_key
    llamacpp_key = raw.get("llamacpp_api_key", "").strip()

    out: dict[str, Any] = {
        k: v for k, v in raw.items() if k not in ("gemini_api_key", "llamacpp_api_key")
    }
    out["has_gemini_key"] = bool(effective_key)
    out["gemini_key_source"] = "settings" if saved_key else ("env" if env_key else "none")
    out["gemini_api_key_masked"] = _mask_secret(effective_key)
    out["has_llamacpp_key"] = bool(llamacpp_key)
    out["llamacpp_api_key_masked"] = _mask_secret(llamacpp_key)
    return out


@router.get("/settings")
def get_settings():
    """Return current app settings with API keys masked."""
    return _public_settings(db.get_all_settings())


@router.put("/settings")
async def put_settings(req: SettingsUpdateRequest):
    """Persist settings updates and return the sanitized public settings."""
    updates: dict[str, str] = {}
    data = req.model_dump(exclude_none=True)

    if bool(data.pop("clear_gemini_key", False)):
        updates["gemini_api_key"] = ""
    elif "gemini_api_key" in data:
        key_val = str(data.pop("gemini_api_key")).strip()
        if key_val:
            updates["gemini_api_key"] = key_val
    data.pop("gemini_api_key", None)

    clear_llama_key = bool(data.pop("clear_llamacpp_key", False))
    if clear_llama_key:
        updates["llamacpp_api_key"] = ""
    elif "llamacpp_api_key" in data:
        key_val = str(data.pop("llamacpp_api_key")).strip()
        if key_val:
            updates["llamacpp_api_key"] = key_val
    data.pop("llamacpp_api_key", None)

    for key, val in data.items():
        if key in config.ALLOWED_SETTING_KEYS:
            updates[key] = str(val)

    if "manga_layout" in updates and updates["manga_layout"] not in {"spread", "single", "vertical"}:
        updates["manga_layout"] = "spread"

    saved = db.update_settings(updates)
    if clear_llama_key or (set(updates.keys()) & _LLM_LIFECYCLE_KEYS):
        if (
            saved.get("llm_provider", "").strip().lower() == "llamacpp"
            and saved.get("llamacpp_autostart", "true").strip().lower()
            not in {"false", "0", "no", "off"}
        ):
            await asyncio.to_thread(llamacpp_manager.ensure_managed_server_running, saved, 0.0)
        else:
            await asyncio.to_thread(llamacpp_manager.stop_managed_server)
    return _public_settings(saved)


@router.get("/llm/status")
async def get_llm_status():
    """Return non-blocking background startup and readiness status for the active LLM."""
    return await asyncio.to_thread(llamacpp_manager.get_status, db.get_all_settings())


@router.get("/llm/llamacpp-models")
async def get_llamacpp_models(url: str | None = None):
    """Query a llama.cpp / OpenAI-compatible server for available models."""
    settings = db.get_all_settings()
    base_url = (url or settings.get("llamacpp_url") or "http://127.0.0.1:8080").strip()
    api_key = settings.get("llamacpp_api_key", "").strip()
    try:
        models = await llm.list_llamacpp_models(base_url, api_key=api_key)
        return {
            "reachable": True,
            "url": base_url,
            "models": models,
            "suggested": llm.pick_default_ollama_model(models),
        }
    except llm.LLMError as exc:
        return {
            "reachable": False,
            "url": base_url,
            "models": [],
            "suggested": None,
            "error": str(exc),
        }


@router.get("/llm/ollama-models")
async def get_ollama_models(url: str | None = None):
    """List locally installed Ollama models from the configured or supplied URL."""
    settings = db.get_all_settings()
    base_url = (url or settings.get("ollama_url") or "http://localhost:11434").strip()
    try:
        models = await llm.list_ollama_models(base_url)
        return {
            "reachable": True,
            "url": base_url,
            "models": models,
            "suggested": llm.pick_default_ollama_model(models),
        }
    except llm.LLMError as exc:
        return {
            "reachable": False,
            "url": base_url,
            "models": [],
            "suggested": None,
            "error": str(exc),
        }


@router.post("/llm/test")
async def test_llm(req: LLMTestRequest | None = None):
    """Test connectivity and a minimal response from the selected LLM provider."""
    merged = db.get_all_settings()
    if req is not None:
        overrides = req.model_dump(exclude_none=True)
        for k, v in overrides.items():
            if k in ("gemini_api_key", "llamacpp_api_key") and not str(v).strip():
                continue
            merged[k] = str(v)
    return await llm.test_connection(merged)
