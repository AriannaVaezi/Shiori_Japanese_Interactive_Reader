"""Context-aware inline grammar assistant and persistent Q&A threads."""

from __future__ import annotations

import json
from typing import Any

from app import db, llm

_GRAMMAR_SYSTEM_PROMPT = """You are a friendly, concise Japanese grammar tutor helping an English-speaking learner read a Japanese novel.

Rules:
- Write ALL translations and explanations strictly in clear, concise English (NEVER use Chinese, and do not write prose explanations in Japanese unless the learner explicitly asks you to reply in Japanese).
- Quote only the relevant Japanese words, particles, and grammar patterns from the passage inline, followed by their English explanation.
- Keep answers brief and scannable so the learner can return to reading quickly.

Example format:
**Meaning in context:** The morning breeze felt cool by the river.
- **川辺で (かわべで)**: "by the riverside" (`で` marks the location).
- **涼しく感じた**: adverbial `涼しく` + `感じた` ("felt cool").
- **Nuance:** Calm, reflective tone fitting a quiet opening scene."""

_TRANSLATE_SYSTEM_PROMPT = """You are a precise, natural Japanese-to-English translator helping a learner understand a passage while reading a novel or manga.

Rules:
- Write your entire response strictly in clear, natural English (NEVER use Chinese).
- Focus directly on giving the accurate meaning of the highlighted Japanese text **in the context of the surrounding scene** (resolving implied subjects, speaker tone, idioms, or omitted pronouns from `[Surrounding Context]`).
- Keep the response brief and scannable so the learner can keep reading; do not give a long grammar lecture unless asked.

Format:
**In-Context Translation:** <natural English translation of the highlighted portion as meant in this scene>
- **Context & Nuance:** 1–3 concise bullets explaining implied subject/speaker, tone, idiom, or literal wording if non-obvious."""


def _load_sentence_context(
    book_id: int,
    chapter_index: int,
    sent_id: str,
    fallback_sentence: str = "",
) -> tuple[str, str]:
    """Return (target_sentence, surrounding_context_text) from chapters or manga_pages."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT sentences_json FROM chapters WHERE book_id = ? AND chapter_index = ?",
            (book_id, chapter_index),
        ).fetchone()
        manga_row = None
        if row is None:
            manga_row = conn.execute(
                "SELECT ocr_json FROM manga_pages WHERE book_id = ? AND page_index = ?",
                (book_id, chapter_index),
            ).fetchone()

    if row and row["sentences_json"]:
        sentences: list[dict[str, str]] = json.loads(row["sentences_json"])
        idx = next((i for i, s in enumerate(sentences) if s.get("sent_id") == sent_id), None)
        if idx is None:
            return fallback_sentence, fallback_sentence

        target = sentences[idx]["text"]
        start = max(0, idx - 2)
        end = min(len(sentences), idx + 2)
        ctx_lines = []
        for i in range(start, end):
            prefix = ">>> " if i == idx else "    "
            ctx_lines.append(f"{prefix}{sentences[i]['text']}")
        return target, "\n".join(ctx_lines)

    if manga_row and manga_row["ocr_json"]:
        try:
            ocr_data = json.loads(manga_row["ocr_json"])
        except Exception:
            return fallback_sentence, fallback_sentence
        blocks = [b for b in (ocr_data.get("blocks") or []) if (b.get("text") or "").strip()]
        if not blocks:
            return fallback_sentence, fallback_sentence

        idx = next((i for i, b in enumerate(blocks) if b.get("block_id") == sent_id), None)
        if idx is not None:
            target = blocks[idx]["text"]
            start = max(0, idx - 3)
            end = min(len(blocks), idx + 3)
            ctx_lines = [
                f"{'>>> ' if i == idx else '    '}{blocks[i]['text']}"
                for i in range(start, end)
            ]
            return target, "\n".join(ctx_lines)

        # Manual crop or edited text on the same manga page: include page bubbles as context
        target = fallback_sentence.strip()
        ctx_lines = [f"    {b['text']}" for b in blocks[:8]]
        if target:
            ctx_lines.append(f">>> {target}")
        return target, "\n".join(ctx_lines) if ctx_lines else target

    return fallback_sentence, fallback_sentence


def _row_to_thread(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "book_id": row["book_id"],
        "chapter_index": row["chapter_index"],
        "sent_id": row["sent_id"],
        "target_text": row["target_text"],
        "sentence_text": row["sentence_text"],
        "context_text": row["context_text"],
        "messages": json.loads(row["messages_json"]),
        "updated_at": row["updated_at"],
    }


def get_chapter_threads(book_id: int, chapter_index: int) -> list[dict[str, Any]]:
    """Return all saved grammar Q&A threads for a chapter."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM grammar_threads
            WHERE book_id = ? AND chapter_index = ?
            ORDER BY updated_at DESC
            """,
            (book_id, chapter_index),
        ).fetchall()
    return [_row_to_thread(r) for r in rows]


def delete_thread(thread_id: int) -> bool:
    """Delete a saved grammar thread by ID."""
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM grammar_threads WHERE id = ?", (thread_id,))
        return cur.rowcount > 0


async def ask_grammar(
    *,
    book_id: int,
    chapter_index: int,
    sent_id: str,
    target_text: str = "",
    sentence_text: str = "",
    question: str = "",
    mode: str = "grammar",
) -> dict[str, Any]:
    """Ask a grammar, translation, or nuance question about a sentence and persist the thread."""
    norm_mode = "translate" if (mode or "").strip().lower() == "translate" else "grammar"
    clean_target = (target_text or "").strip()
    clean_sentence_input = (sentence_text or "").strip()
    resolved_sentence, context_text = _load_sentence_context(
        book_id, chapter_index, sent_id, fallback_sentence=clean_sentence_input or clean_target
    )
    if not resolved_sentence:
        resolved_sentence = clean_sentence_input or clean_target
        context_text = resolved_sentence
    elif clean_sentence_input and len(clean_sentence_input) > len(resolved_sentence):
        resolved_sentence = clean_sentence_input
        if resolved_sentence not in context_text:
            context_text = f"{context_text}\n>>> {resolved_sentence}".strip()

    with db.get_conn() as conn:
        existing = conn.execute(
            """
            SELECT * FROM grammar_threads
            WHERE book_id = ? AND chapter_index = ? AND sent_id = ? AND target_text = ?
            """,
            (book_id, chapter_index, sent_id, clean_target),
        ).fetchone()

    prior_messages: list[dict[str, str]] = (
        json.loads(existing["messages_json"]) if existing else []
    )
    clean_question = (question or "").strip()

    has_requested_mode = (
        any(m.get("mode") == "translate" for m in prior_messages)
        if norm_mode == "translate"
        else any(m.get("mode", "grammar") == "grammar" for m in prior_messages)
    )

    # If the user clicked ? or Translate without typing a follow-up and this mode already ran, return it
    if existing and not clean_question and prior_messages and has_requested_mode:
        return {
            "llm_unavailable": False,
            "reused": True,
            "thread": _row_to_thread(existing),
        }

    focus_line = f"Focus phrase: 「{clean_target}」\n" if clean_target else ""
    if clean_question:
        ask_line = clean_question
        display_user_msg = clean_question
    elif norm_mode == "translate":
        ask_line = (
            f"In natural English only (no Chinese), translate and explain the meaning of the highlighted portion 「{clean_target}」 in the context of this scene."
            if clean_target
            else "In natural English only (no Chinese), translate and explain the meaning of this sentence in the context of this scene."
        )
        display_user_msg = (
            f'Translate "{clean_target}" in context.'
            if clean_target
            else "Translate this sentence in context."
        )
    else:
        ask_line = (
            "In concise English only (no Chinese), translate this sentence in context and break down the key grammar, particles, conjugations, and nuance."
        )
        display_user_msg = (
            f'Explain the grammar and nuance of "{clean_target}" in this sentence.'
            if clean_target
            else "Break down the grammar and nuance of this sentence."
        )

    if not prior_messages:
        llm_prompt = (
            f"[Surrounding Context]\n{context_text}\n\n"
            f"[Target Sentence]\n{resolved_sentence}\n"
            f"{focus_line}\n"
            f"[Question]\n{ask_line}"
        )
        llm_messages = [{"role": "user", "content": llm_prompt}]
    else:
        # Rebuild full context on turn 1 so follow-ups retain passage awareness
        first_user = (
            f"[Surrounding Context]\n{context_text}\n\n"
            f"[Target Sentence]\n{resolved_sentence}\n"
            + (f"Focus phrase: 「{clean_target}」\n\n" if clean_target else "\n")
            + f"[Question]\n{prior_messages[0]['content']}"
        )
        llm_messages = [{"role": "user", "content": first_user}]
        for msg in prior_messages[1:]:
            llm_messages.append({"role": msg["role"], "content": msg["content"]})
        llm_messages.append({"role": "user", "content": ask_line})

    system_prompt = (
        _TRANSLATE_SYSTEM_PROMPT if norm_mode == "translate" else _GRAMMAR_SYSTEM_PROMPT
    )
    settings = db.get_all_settings()
    try:
        answer_text, provider, model = await llm.generate_text(
            settings=settings,
            system_prompt=system_prompt,
            messages=llm_messages,
            expect_json=False,
        )
    except llm.LLMError as exc:
        stub_thread = (
            _row_to_thread(existing)
            if existing
            else {
                "id": None,
                "book_id": book_id,
                "chapter_index": chapter_index,
                "sent_id": sent_id,
                "target_text": clean_target,
                "sentence_text": resolved_sentence,
                "context_text": context_text,
                "messages": prior_messages,
                "updated_at": db.utc_now_iso(),
            }
        )
        return {
            "llm_unavailable": True,
            "error": str(exc),
            "thread": stub_thread,
        }

    now = db.utc_now_iso()
    updated_messages = [
        *prior_messages,
        {"role": "user", "content": display_user_msg, "mode": norm_mode, "created_at": now},
        {
            "role": "assistant",
            "content": answer_text,
            "mode": norm_mode,
            "provider": provider,
            "model": model,
            "created_at": now,
        },
    ]

    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO grammar_threads
                (book_id, chapter_index, sent_id, target_text, sentence_text, context_text, messages_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(book_id, chapter_index, sent_id, target_text) DO UPDATE SET
                sentence_text = excluded.sentence_text,
                context_text = excluded.context_text,
                messages_json = excluded.messages_json,
                updated_at = excluded.updated_at
            """,
            (
                book_id,
                chapter_index,
                sent_id,
                clean_target,
                resolved_sentence,
                context_text,
                json.dumps(updated_messages, ensure_ascii=False),
                now,
            ),
        )
        saved_row = conn.execute(
            """
            SELECT * FROM grammar_threads
            WHERE book_id = ? AND chapter_index = ? AND sent_id = ? AND target_text = ?
            """,
            (book_id, chapter_index, sent_id, clean_target),
        ).fetchone()

    db.record_trouble_event(
        item_type="grammar",
        item_key=f"{book_id}:{chapter_index}:{sent_id}:{clean_target}",
        display_text=clean_target or resolved_sentence[:36],
        book_id=book_id,
        chapter_index=chapter_index,
        sent_id=sent_id,
        sent_text=resolved_sentence,
        grammar_delta=1,
        score_delta=2.5,
    )

    return {
        "llm_unavailable": False,
        "reused": False,
        "thread": _row_to_thread(saved_row),
    }
