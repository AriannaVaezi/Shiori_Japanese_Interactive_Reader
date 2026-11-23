"""Adaptive chapter/timer mini-game routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import quiz_service

router = APIRouter(prefix="/api/quiz", tags=["quiz"])


class QuizGenerateRequest(BaseModel):
    book_id: int
    chapter_index: int
    trigger_type: str = "chapter"
    max_questions: int = 5
    session_started_at: str | None = None


class QuizResultItem(BaseModel):
    question_id: str = ""
    kind: str = ""
    trouble_item_id: int | None = None
    trouble_item_ids: list[int] = []
    correct: bool
    skipped: bool = False


class QuizSubmitRequest(BaseModel):
    book_id: int | None = None
    chapter_index: int | None = None
    trigger_type: str = "chapter"
    results: list[QuizResultItem]


class OutputEvalRequest(BaseModel):
    target_word: str
    reading: str = ""
    item_type: str = "word"
    user_sentence: str
    meaning_hint: str = ""


@router.post("/generate")
def generate_quiz(req: QuizGenerateRequest):
    """Generate up to 5 adaptive review questions for the current chapter/session."""
    return quiz_service.generate_quiz(
        book_id=req.book_id,
        chapter_index=req.chapter_index,
        trigger_type=req.trigger_type,
        max_questions=max(1, min(req.max_questions, 10)),
        session_started_at=req.session_started_at,
    )


@router.post("/submit")
def submit_quiz(req: QuizSubmitRequest):
    """Submit completed quiz answers, update trouble scores/retirement, and log session."""
    return quiz_service.submit_quiz(
        book_id=req.book_id,
        chapter_index=req.chapter_index,
        trigger_type=req.trigger_type,
        results=[r.model_dump() for r in req.results],
    )


@router.post("/evaluate-output")
async def evaluate_output(req: OutputEvalRequest):
    """Evaluate a learner-written Japanese sentence for the 1-sentence output quiz prompt."""
    return await quiz_service.evaluate_output_sentence(
        target_word=req.target_word,
        reading=req.reading,
        item_type=req.item_type,
        user_sentence=req.user_sentence,
        meaning_hint=req.meaning_hint,
    )


@router.get("/history")
def get_quiz_history(book_id: int | None = None, limit: int = 10):
    """Return recent mini-game sessions."""
    return {
        "sessions": quiz_service.list_quiz_history(
            book_id=book_id, limit=max(1, min(limit, 50))
        )
    }
