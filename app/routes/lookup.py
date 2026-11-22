"""Two-step vocabulary lookup and trouble tracking routes."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import lookup_service

router = APIRouter(prefix="/api", tags=["lookup"])


class JJLookupRequest(BaseModel):
    lemma: str
    reading: str = ""
    surface: str = ""
    pos: str = ""
    sentence_text: str = ""
    book_id: int | None = None
    chapter_index: int | None = None
    sent_id: str | None = None
    force_regenerate: bool = False


class ENLookupRequest(BaseModel):
    lemma: str
    norm_form: str = ""
    surface: str = ""
    reading: str = ""
    book_id: int | None = None
    chapter_index: int | None = None
    sent_id: str | None = None


@router.post("/lookup/jj")
async def lookup_jj(req: JJLookupRequest):
    """Return an easy Japanese (J-J) explanation and simple examples."""
    return await lookup_service.lookup_jj(
        lemma=req.lemma,
        reading=req.reading,
        surface=req.surface,
        pos=req.pos,
        sentence_text=req.sentence_text,
        book_id=req.book_id,
        chapter_index=req.chapter_index,
        sent_id=req.sent_id,
        force_regenerate=req.force_regenerate,
    )


@router.post("/lookup/en")
def lookup_en(req: ENLookupRequest):
    """Reveal offline JMdict English entries and log an English-reveal trouble signal."""
    return lookup_service.lookup_en(
        lemma=req.lemma,
        norm_form=req.norm_form,
        surface=req.surface,
        reading=req.reading,
        book_id=req.book_id,
        chapter_index=req.chapter_index,
        sent_id=req.sent_id,
    )


@router.get("/trouble")
def get_trouble_items(limit: int = 50):
    """Return tracked trouble words and grammar items ordered by trouble_score."""
    return {"items": lookup_service.list_trouble_items(limit=max(1, min(limit, 200)))}
