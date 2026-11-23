"""Application configuration paths and default settings."""

from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
BOOKS_DIR = ROOT_DIR / "books"
DATA_DIR = ROOT_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
JAMDICT_DB_PATH = DATA_DIR / "jamdict.db"
STATIC_DIR = ROOT_DIR / "static"

BOOK_ID_PLACEHOLDER = "__BOOK_ID__"

DEFAULT_SETTINGS: dict[str, str] = {
    "llm_provider": "llamacpp",
    "llamacpp_url": "http://127.0.0.1:8080",
    "llamacpp_model": "",
    "llamacpp_api_key": "",
    "llamacpp_model_path": "",
    "llamacpp_autostart": "true",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "",
    "gemini_api_key": "",
    "gemini_model": "gemini-2.5-flash",
    "reading_mode": "horizontal",
    "furigana_mode": "all",
    "theme": "paper",
    "font_size": "20",
    "quiz_timer_minutes": "15",
}

ALLOWED_SETTING_KEYS = set(DEFAULT_SETTINGS.keys())
