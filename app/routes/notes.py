"""Saved quotes, colored highlights, and free-form reading notes routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services import notes_service

router = APIRouter(prefix="/api/notes", tags=["notes"])


class NoteCreateRequest(BaseModel):
    book_id: int
    chapter_index: int
    sent_id: str | None = None
    quote_text: str = ""
    note_text: str = ""
    category: str = "quote"
    color: str = "yellow"
    replace_existing: bool = False
    target_note_ids: list[int] = Field(default_factory=list)


class NoteUnhighlightRequest(BaseModel):
    book_id: int
    chapter_index: int
    sent_id: str | None = None
    quote_text: str = ""
    note_ids: list[int] = Field(default_factory=list)


class NoteUpdateRequest(BaseModel):
    quote_text: str | None = None
    note_text: str | None = None
    category: str | None = None
    color: str | None = None


@router.get("")
def list_notes(
    book_id: int | None = None,
    chapter_index: int | None = None,
    category: str | None = None,
    color: str | None = None,
    limit: int = 200,
):
    """Return saved book highlights and study notes."""
    return {
        "notes": notes_service.list_notes(
            book_id=book_id,
            chapter_index=chapter_index,
            category=category,
            color=color,
            limit=max(1, min(limit, 500)),
        )
    }


@router.post("")
def create_note(req: NoteCreateRequest):
    """Save a highlighted passage or free-form reading note."""
    try:
        note = notes_service.create_note(
            book_id=req.book_id,
            chapter_index=req.chapter_index,
            sent_id=req.sent_id,
            quote_text=req.quote_text,
            note_text=req.note_text,
            category=req.category,
            color=req.color,
            replace_existing=req.replace_existing,
            target_note_ids=req.target_note_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"note": note}


@router.post("/unhighlight")
def unhighlight_notes(req: NoteUnhighlightRequest):
    """Remove highlight marks; deletes notes without user commentary, clears color otherwise."""
    return notes_service.unhighlight_notes(
        book_id=req.book_id,
        chapter_index=req.chapter_index,
        sent_id=req.sent_id,
        quote_text=req.quote_text,
        note_ids=req.note_ids,
    )


@router.put("/{note_id}")
def update_note(note_id: int, req: NoteUpdateRequest):
    """Edit an existing quote, highlight color, or reading note."""
    try:
        note = notes_service.update_note(
            note_id,
            quote_text=req.quote_text,
            note_text=req.note_text,
            category=req.category,
            color=req.color,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if note is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"note": note}


@router.delete("/{note_id}")
def delete_note(note_id: int):
    """Delete a saved quote or reading note."""
    if not notes_service.delete_note(note_id):
        raise HTTPException(status_code=404, detail="Note not found")
    return {"deleted": True}
