"""Convenience launcher for the Japanese Interactive EPUB Reader."""

from __future__ import annotations

import uvicorn


def main() -> None:
    print("Starting Japanese Interactive EPUB Reader at http://127.0.0.1:8000")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
