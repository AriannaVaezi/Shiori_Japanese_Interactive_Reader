"""Context-aware inline grammar Q&A routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import grammar_service

router = APIRouter(prefix="/api/grammar", tags=["grammar"])


class GrammarAskRequest(BaseModel):
    book_id: int
    chapter_index: int
    sent_id: str
    target_text: str = ""
    sentence_text: str = ""
    question: str = ""
    mode: str = "grammar"


@router.post("/ask")
async def ask_grammar(req: GrammarAskRequest):
    """Ask a grammar, translation, or nuance question about a sentence and save the conversation."""
    return await grammar_service.ask_grammar(
        book_id=req.book_id,
        chapter_index=req.chapter_index,
        sent_id=req.sent_id,
        target_text=req.target_text,
        sentence_text=req.sentence_text,
        question=req.question,
        mode=req.mode,
    )


@router.get("/threads/{book_id}/{chapter_index}")
def list_chapter_threads(book_id: int, chapter_index: int):
    """Return all saved grammar threads for a given book chapter."""
    return {"threads": grammar_service.get_chapter_threads(book_id, chapter_index)}


@router.delete("/threads/{thread_id}")
def remove_thread(thread_id: int):
    """Delete a saved grammar thread."""
    if not grammar_service.delete_thread(thread_id):
        raise HTTPException(status_code=404, detail="Grammar thread not found")
    return {"deleted": True}
