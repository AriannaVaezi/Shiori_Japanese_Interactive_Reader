"""Convenience launcher for the Japanese Interactive EPUB Reader."""

from __future__ import annotations

import atexit

import uvicorn

from app import db, llamacpp_manager


def main() -> None:
    db.init_db()
    atexit.register(llamacpp_manager.stop_managed_server)

    try:
        print("Starting Shiori at http://127.0.0.1:8000 (local LLM loads in background if enabled)")
        uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
    finally:
        llamacpp_manager.stop_managed_server()


if __name__ == "__main__":
    main()
