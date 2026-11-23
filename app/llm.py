"""Multi-provider LLM client supporting llama.cpp (OpenAI-compatible), Ollama, and Gemini API."""

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
_THINK_BLOCK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


class LLMError(Exception):
    """Base error for LLM generation issues."""


class LLMConfigError(LLMError):
    """Raised when the selected LLM provider is missing required configuration."""


class LLMUnavailableError(LLMError):
    """Raised when the LLM backend cannot be reached or returned an error."""


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> reasoning blocks emitted by reasoning models."""
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    if "<think>" in cleaned.lower():
        cleaned = re.split(r"<think>", cleaned, flags=re.IGNORECASE)[0].strip()
    return cleaned


def pick_default_ollama_model(models: list[str]) -> str | None:
    """Choose the best Japanese-capable model from a list of model names."""
    if not models:
        return None
    for prefix in _PREFERRED_OLLAMA_PREFIXES:
        for name in models:
            if name.lower().startswith(prefix):
                return name
    return models[0]


async def list_llamacpp_models(base_url: str, api_key: str = "") -> list[str]:
    """Query llama.cpp / OpenAI-compatible GET /v1/models."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        url = f"{root}/models"
    else:
        url = f"{root}/v1/models"

    headers = {}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise LLMUnavailableError(f"Could not reach llama.cpp server at {base_url}: {exc}") from exc

    models: list[str] = []
    for item in data.get("data") or []:
        if isinstance(item, dict) and item.get("id"):
            models.append(str(item["id"]))
    if not models:
        for item in data.get("models") or []:
            if isinstance(item, dict):
                name = item.get("id") or item.get("name") or item.get("model")
                if name:
                    models.append(str(name))
    return models


async def _llamacpp_chat(
    base_url: str,
    model: str,
    system_prompt: str,
    messages: list[dict[str, str]],
    expect_json: bool = False,
    api_key: str = "",
) -> tuple[str, str]:
    root = base_url.rstrip("/")
    url = f"{root}/chat/completions" if root.endswith("/v1") else f"{root}/v1/chat/completions"

    headers = {}
    if api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"

    payload: dict[str, Any] = {
        "messages": [{"role": "system", "content": system_prompt}, *messages],
        "stream": False,
        "temperature": 0.3,
    }
    if model.strip():
        payload["model"] = model.strip()
    if expect_json:
        payload["response_format"] = {"type": "json_object"}

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code in (400, 422) and "response_format" in payload:
                payload.pop("response_format", None)
                resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise LLMUnavailableError(f"llama.cpp request failed ({base_url}): {exc}") from exc

    choices = data.get("choices") or []
    raw_content = ""
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        raw_content = str(msg.get("content") or choices[0].get("text") or "")

    cleaned = _strip_think_tags(raw_content)
    if not cleaned:
        raise LLMUnavailableError("llama.cpp returned an empty response.")

    model_used = str(data.get("model") or model or "llama.cpp").strip()
    return cleaned, model_used


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

    content = _strip_think_tags(str((data.get("message") or {}).get("content", "")))
    if not content:
        raise LLMUnavailableError("Ollama returned an empty response.")
    return content


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
    provider = (settings.get("llm_provider") or "llamacpp").strip().lower()

    if provider == "llamacpp":
        base_url = (settings.get("llamacpp_url") or "http://127.0.0.1:8080").strip()
        api_key = (settings.get("llamacpp_api_key") or "").strip()
        model = (settings.get("llamacpp_model") or "").strip()
        if not model:
            try:
                models = await list_llamacpp_models(base_url, api_key=api_key)
                picked = pick_default_ollama_model(models)
                if picked:
                    model = picked
            except LLMError:
                pass
        text, model_used = await _llamacpp_chat(
            base_url, model, system_prompt, messages, expect_json=expect_json, api_key=api_key
        )
        return text, "llamacpp", model_used

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
            "provider": settings.get("llm_provider", "llamacpp"),
            "error": str(exc),
        }


def extract_json_object(text: str) -> dict[str, Any]:
    """Parse a JSON object from LLM output, stripping think tags and code fences if needed."""
    cleaned = _strip_think_tags(text)
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
