"""Application settings and LLM provider testing routes."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app import config, db, llm

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsUpdateRequest(BaseModel):
    llm_provider: str | None = None
    ollama_url: str | None = None
    ollama_model: str | None = None
    gemini_api_key: str | None = None
    clear_gemini_key: bool = False
    gemini_model: str | None = None
    reading_mode: str | None = None
    furigana_mode: str | None = None
    theme: str | None = None
    font_size: str | None = None


class LLMTestRequest(BaseModel):
    llm_provider: str | None = None
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
    out: dict[str, Any] = {k: v for k, v in raw.items() if k != "gemini_api_key"}
    out["has_gemini_key"] = bool(effective_key)
    out["gemini_key_source"] = "settings" if saved_key else ("env" if env_key else "none")
    out["gemini_api_key_masked"] = _mask_secret(effective_key)
    return out


@router.get("/settings")
def get_settings():
    """Return current app settings with Gemini API key masked."""
    return _public_settings(db.get_all_settings())


@router.put("/settings")
def put_settings(req: SettingsUpdateRequest):
    """Persist settings updates and return the sanitized public settings."""
    updates: dict[str, str] = {}
    data = req.model_dump(exclude_none=True)
    clear_key = bool(data.pop("clear_gemini_key", False))

    if clear_key:
        updates["gemini_api_key"] = ""
    elif "gemini_api_key" in data:
        key_val = str(data.pop("gemini_api_key")).strip()
        if key_val:
            updates["gemini_api_key"] = key_val
    data.pop("gemini_api_key", None)

    for key, val in data.items():
        if key in config.ALLOWED_SETTING_KEYS:
            updates[key] = str(val)

    saved = db.update_settings(updates)
    return _public_settings(saved)


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
            if k == "gemini_api_key" and not str(v).strip():
                continue
            merged[k] = str(v)
    return await llm.test_connection(merged)
