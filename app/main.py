"""FastAPI application entrypoint."""

from __future__ import annotations

import hashlib
import mimetypes
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import config, db, llamacpp_manager
from app.routes import books, grammar, lookup, manga, notes, quiz, settings
from app.services import books_service, manga_service

_STATIC_MIME_OVERRIDES = {
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8",
}


def _static_asset_version() -> str:
    """Compute a short deterministic version token across static frontend assets."""
    static_root = config.STATIC_DIR
    if not static_root.is_dir():
        return "0"
    digest = hashlib.sha256()
    for path in sorted(static_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(static_root).as_posix()
        st = path.stat()
        digest.update(f"{rel}:{st.st_mtime_ns}:{st.st_size}\n".encode("utf-8"))
    return digest.hexdigest()[:12]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    llamacpp_manager.start_managed_server_async(db.get_all_settings())
    books_service.scan_books_folder()
    manga_service.scan_manga_folder()
    try:
        yield
    finally:
        llamacpp_manager.stop_managed_server()


app = FastAPI(title="Japanese Interactive EPUB Reader", lifespan=lifespan)


@app.middleware("http")
async def disable_static_caching(request: Request, call_next):
    """Prevent stale browser caching of index HTML and ES module assets."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


app.include_router(books.router)
app.include_router(manga.router)
app.include_router(lookup.router)
app.include_router(grammar.router)
app.include_router(notes.router)
app.include_router(quiz.router)
app.include_router(settings.router)

config.STATIC_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/static/v/{version}/{asset_path:path}")
def serve_versioned_static(version: str, asset_path: str):
    """Serve versioned static files with explicit MIME types and no-store cache headers."""
    static_root = config.STATIC_DIR.resolve()
    candidate = (static_root / asset_path).resolve()
    if not candidate.is_relative_to(static_root) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Static asset not found")
    ext = candidate.suffix.lower()
    media_type = (
        _STATIC_MIME_OVERRIDES.get(ext)
        or mimetypes.guess_type(str(candidate))[0]
        or "application/octet-stream"
    )
    return FileResponse(
        candidate,
        media_type=media_type,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.get("/")
def serve_index():
    """Serve the single-page frontend entrypoint with cache-busted CSS and ES module URLs."""
    ver = _static_asset_version()
    index_path = config.STATIC_DIR / "index.html"
    raw_html = index_path.read_text(encoding="utf-8")
    busted_html = (
        raw_html.replace('href="/static/css/app.css"', f'href="/static/v/{ver}/css/app.css"')
        .replace('src="/static/js/main.js"', f'src="/static/v/{ver}/js/main.js"')
    )
    return HTMLResponse(
        content=busted_html,
        headers={"Cache-Control": "no-store, max-age=0"},
    )
