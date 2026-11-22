"""Book library, chapter delivery, asset streaming, and reading progress routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from app.services import books_service

router = APIRouter(prefix="/api/books", tags=["books"])


class ProgressUpdateRequest(BaseModel):
    chapter_index: int
    scroll_ratio: float = 0.0
    last_sent_id: str | None = None
    reading_seconds_delta: int = 0


@router.get("")
def list_books():
    """Return all imported books with chapter TOC and reading progress."""
    return {"books": books_service.list_books()}


@router.post("/scan")
def scan_books():
    """Rescan the books/ folder for new or updated EPUB files."""
    books = books_service.scan_books_folder()
    return {"books": books}


@router.get("/{book_id}")
def get_book(book_id: int):
    """Return metadata, chapter list, and progress for one book."""
    book = books_service.get_book_detail(book_id)
    if book is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return book


@router.get("/{book_id}/chapters/{chapter_index}")
def get_chapter(book_id: int, chapter_index: int):
    """Return tokenized HTML and metadata for a specific chapter."""
    chapter = books_service.get_chapter(book_id, chapter_index)
    if chapter is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return chapter


@router.get("/{book_id}/assets/{asset_path:path}")
def get_asset(book_id: int, asset_path: str):
    """Stream an image or asset directly from the book's EPUB archive."""
    asset = books_service.get_book_asset(book_id, asset_path)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    data, mime_type = asset
    return Response(
        content=data,
        media_type=mime_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.put("/{book_id}/progress")
def update_progress(book_id: int, req: ProgressUpdateRequest):
    """Persist reading position and incremental reading seconds."""
    prog = books_service.save_progress(
        book_id=book_id,
        chapter_index=req.chapter_index,
        scroll_ratio=req.scroll_ratio,
        last_sent_id=req.last_sent_id,
        reading_seconds_delta=req.reading_seconds_delta,
    )
    if prog is None:
        raise HTTPException(status_code=404, detail="Book not found")
    return {"progress": prog}
