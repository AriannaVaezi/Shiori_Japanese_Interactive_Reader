"""FastAPI application entrypoint."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import config, db
from app.routes import books, grammar, lookup, settings
from app.services import books_service


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    books_service.scan_books_folder()
    yield


app = FastAPI(title="Japanese Interactive EPUB Reader", lifespan=lifespan)

app.include_router(books.router)
app.include_router(lookup.router)
app.include_router(grammar.router)
app.include_router(settings.router)

config.STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.get("/")
def serve_index():
    """Serve the single-page frontend entrypoint."""
    return FileResponse(config.STATIC_DIR / "index.html")
