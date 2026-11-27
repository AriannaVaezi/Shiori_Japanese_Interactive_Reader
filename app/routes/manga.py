"""Manga library, page image streaming, and CPU OCR API routes."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field

from app.services import manga_ocr_service, manga_service
from app.tokenizer import tokenize_plain_text

router = APIRouter(prefix="/api/manga", tags=["manga"])


class MangaOcrRequest(BaseModel):
    force: bool = False


class MangaCropOcrRequest(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)


class MangaTokenizeRequest(BaseModel):
    text: str


@router.get("")
def list_manga():
    """Return all imported manga volumes and whether local OCR is installed."""
    return {
        "manga": manga_service.list_manga(),
        "ocr_available": manga_ocr_service.is_ocr_available(),
    }


@router.post("/scan")
def scan_manga():
    """Rescan the manga/ folder recursively for EPUB and CBZ manga volumes."""
    return {
        "manga": manga_service.scan_manga_folder(),
        "ocr_available": manga_ocr_service.is_ocr_available(),
    }


@router.post("/tokenize")
def tokenize_manga_text(req: MangaTokenizeRequest):
    """Tokenize user-edited OCR text into clickable SudachiPy tokens."""
    clean = (req.text or "").strip()
    return {
        "text": clean,
        "tokens": tokenize_plain_text(clean),
    }


@router.get("/{book_id}")
def get_manga(book_id: int):
    """Return metadata, page aspect dimensions, and reading progress for a manga volume."""
    manga = manga_service.get_manga_detail(book_id)
    if manga is None:
        raise HTTPException(status_code=404, detail="Manga volume not found")
    return manga


@router.get("/{book_id}/pages/{page_index}/image")
def get_manga_page_image(book_id: int, page_index: int):
    """Stream an original manga page image without cropping or re-encoding."""
    asset = manga_service.get_manga_page_image(book_id, page_index)
    if asset is None:
        raise HTTPException(status_code=404, detail="Manga page image not found")
    data, mime_type = asset
    return Response(
        content=data,
        media_type=mime_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/{book_id}/screenshot")
async def get_manga_screenshot(book_id: int, pages: str = ""):
    """Download a lossless PNG screenshot of 1 page or 2 stitched pages in visual L->R order."""
    parts = [p.strip() for p in pages.split(",") if p.strip()]
    if not parts or len(parts) > 2:
        raise HTTPException(
            status_code=400,
            detail="Provide 1 or 2 comma-separated page indices in `pages`.",
        )
    indices: list[int] = []
    for part in parts:
        try:
            idx = int(part)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Page indices in `pages` must be non-negative integers.",
            ) from exc
        if idx < 0:
            raise HTTPException(
                status_code=400,
                detail="Page indices in `pages` must be non-negative integers.",
            )
        indices.append(idx)

    rendered = await asyncio.to_thread(manga_service.render_manga_screenshot, book_id, indices)
    if rendered is None:
        raise HTTPException(status_code=404, detail="Manga page not found")
    png_bytes, utf8_name, ascii_name = rendered
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(utf8_name)}'
            ),
            "Cache-Control": "no-store, max-age=0",
        },
    )


@router.post("/{book_id}/pages/{page_index}/ocr")
async def ocr_manga_page(book_id: int, page_index: int, req: MangaOcrRequest | None = None):
    """Run (or return cached) CPU OCR + SudachiPy tokens for all text boxes on a manga page."""
    force = bool(req.force) if req else False
    res = await asyncio.to_thread(manga_service.ocr_manga_page, book_id, page_index, force)
    if res is None:
        raise HTTPException(status_code=404, detail="Manga page not found")
    return res


@router.post("/{book_id}/pages/{page_index}/ocr-crop")
async def ocr_manga_crop(book_id: int, page_index: int, req: MangaCropOcrRequest):
    """Run CPU OCR + SudachiPy tokenization on a user-selected crop rectangle."""
    res = await asyncio.to_thread(
        manga_service.ocr_manga_crop,
        book_id,
        page_index,
        req.x,
        req.y,
        req.w,
        req.h,
    )
    if res is None:
        raise HTTPException(status_code=404, detail="Manga page not found")
    return res
