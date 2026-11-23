"""Convenience launcher for the Japanese Interactive EPUB Reader."""

from __future__ import annotations

import atexit

import uvicorn

from app import db, llamacpp_manager


def main() -> None:
    db.init_db()
    settings = db.get_all_settings()
    atexit.register(llamacpp_manager.stop_managed_server)

    try:
        llamacpp_manager.ensure_managed_server_running(settings)
        print("Starting Japanese Interactive EPUB Reader at http://127.0.0.1:8000")
        uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
    finally:
        llamacpp_manager.stop_managed_server()


if __name__ == "__main__":
    main()
