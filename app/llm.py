"""Dual LLM client supporting local Ollama and Google Gemini API."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

import httpx
from google import genai
from google.genai import types

_PREFERRED_OLLAMA_PREFIXES = (
    "qwen3",
    "qwen2.5",
    "gemma3",
    "gemma2",
    "swallow",
    "elyza",
    "llama3",
)


class LLMError(Exception):
    """Base error for LLM generation issues."""


class LLMConfigError(LLMError):
    """Raised when the selected LLM provider is missing required configuration."""


class LLMUnavailableError(LLMError):
    """Raised when the LLM backend cannot be reached or returned an error."""


def pick_default_ollama_model(models: list[str]) -> str | None:
    """Choose the best Japanese-capable Ollama model from an installed model list."""
    if not models:
        return None
    for prefix in _PREFERRED_OLLAMA_PREFIXES:
        for name in models:
            if name.lower().startswith(prefix):
                return name
    return models[0]


async def list_ollama_models(base_url: str) -> list[str]:
    """Query Ollama /api/tags for available local models."""
    url = f"{base_url.rstrip('/')}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise LLMUnavailableError(f"Could not reach Ollama at {base_url}: {exc}") from exc

    return [m["name"] for m in data.get("models", []) if isinstance(m, dict) and m.get("name")]


async def _ollama_chat(
    base_url: str,
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    expect_json: bool = False,
) -> str:
    url = f"{base_url.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}, *messages],
        "stream": False,
        "options": {"temperature": 0.3},
    }
    if expect_json:
        payload["format"] = "json"

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise LLMUnavailableError(f"Ollama request failed ({base_url}): {exc}") from exc

    content = (data.get("message") or {}).get("content", "")
    if not content:
        raise LLMUnavailableError("Ollama returned an empty response.")
    return str(content).strip()


def _sync_gemini_chat(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    expect_json: bool = False,
) -> str:
    client = genai.Client(api_key=api_key)
    contents = [
        types.Content(
            role="model" if m.get("role") == "assistant" else "user",
            parts=[types.Part.from_text(text=m.get("content", ""))],
        )
        for m in messages
    ]
    cfg_kwargs: dict[str, Any] = {
        "system_instruction": system_prompt,
        "temperature": 0.3,
    }
    if expect_json:
        cfg_kwargs["response_mime_type"] = "application/json"

    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(**cfg_kwargs),
    )
    text = getattr(response, "text", None) or ""
    if not text.strip():
        raise LLMUnavailableError("Gemini API returned an empty response.")
    return text.strip()


async def _gemini_chat(
    api_key: str,
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    expect_json: bool = False,
) -> str:
    try:
        return await asyncio.to_thread(
            _sync_gemini_chat, api_key, model, system_prompt, messages, expect_json
        )
    except LLMError:
        raise
    except Exception as exc:
        raise LLMUnavailableError(f"Gemini API request failed: {exc}") from exc


async def generate_text(
    settings: dict[str, str],
    system_prompt: str,
    messages: list[dict[str, str]],
    expect_json: bool = False,
) -> tuple[str, str, str]:
    """Generate a response using the configured LLM provider.

    Returns (text, provider, model_used).
    """
    provider = (settings.get("llm_provider") or "ollama").strip().lower()

    if provider == "gemini":
        api_key = (settings.get("gemini_api_key") or "").strip() or os.environ.get(
            "GEMINI_API_KEY", ""
        ).strip()
        if not api_key:
            raise LLMConfigError(
                "Gemini API key is not set. Add your Google AI Studio key in Settings."
            )
        model = (settings.get("gemini_model") or "gemini-2.5-flash").strip()
        text = await _gemini_chat(api_key, model, system_prompt, messages, expect_json=expect_json)
        return text, "gemini", model

    if provider == "ollama":
        base_url = (settings.get("ollama_url") or "http://localhost:11434").strip()
        model = (settings.get("ollama_model") or "").strip()
        if not model:
            models = await list_ollama_models(base_url)
            picked = pick_default_ollama_model(models)
            if not picked:
                raise LLMConfigError(
                    "No Ollama models installed. Pull one first, e.g. `ollama pull qwen3:8b`."
                )
            model = picked
        text = await _ollama_chat(base_url, model, system_prompt, messages, expect_json=expect_json)
        return text, "ollama", model

    raise LLMConfigError(f"Unsupported LLM provider: {provider}")


async def test_connection(settings: dict[str, str]) -> dict[str, Any]:
    """Run a tiny round-trip check against the configured LLM provider."""
    try:
        text, provider, model = await generate_text(
            settings=settings,
            system_prompt="Reply concisely in Japanese.",
            messages=[{"role": "user", "content": "「了解」とだけ返答してください。"}],
            expect_json=False,
        )
        return {
            "ok": True,
            "provider": provider,
            "model": model,
            "sample": text[:120],
        }
    except LLMError as exc:
        return {
            "ok": False,
            "provider": settings.get("llm_provider", "ollama"),
            "error": str(exc),
        }


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from LLM output, stripping markdown code fences if needed."""
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        parsed = json.loads(cleaned[start : end + 1])
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("LLM response did not contain a valid JSON object.")
