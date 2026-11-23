"""Convenience launcher for the Japanese Interactive EPUB Reader."""

from __future__ import annotations

import atexit

import uvicorn

from app import db
from app.llamacpp_manager import ManagedLlamaServer


def main() -> None:
    db.init_db()
    settings = db.get_all_settings()
    llama_mgr = ManagedLlamaServer(settings)
    atexit.register(llama_mgr.stop)

    try:
        llama_mgr.start_if_needed()
        print("Starting Japanese Interactive EPUB Reader at http://127.0.0.1:8000")
        uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
    finally:
        llama_mgr.stop()


if __name__ == "__main__":
    main()
