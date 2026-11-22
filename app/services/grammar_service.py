"""Context-aware inline grammar assistant and persistent Q&A threads."""

from __future__ import annotations

import json
from typing import Any

from app import db, llm

_GRAMMAR_SYSTEM_PROMPT = """あなたは日本語の小説読解をサポートする親切な文法コーチです。
学習者が小説を読んでいて引っかかった文（または表現・文法項目）について、前後の文脈を踏まえて簡潔で分かりやすく解説してください。

回答のルール:
- 英語で分かりやすく解説しつつ、日本語の文法パターン・例文はしっかり日本語で示すこと（学習者が「日本語だけで」と頼んだ場合は日本語で答える）。
- **文の構造・文法ポイント**（助詞の働き、活用、省略された主語、慣用表現など）を箇条書きで整理すること。
- 長すぎず、読んでいてすぐ小説に戻れる読みやすい長さにまとめること。"""


def _load_sentence_context(
    book_id: int,
    chapter_index: int,
    sent_id: str,
    fallback_sentence: str = "",
) -> tuple[str, str]:
    """Return (target_sentence, surrounding_context_text) from chapters.sentences_json."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT sentences_json FROM chapters WHERE book_id = ? AND chapter_index = ?",
            (book_id, chapter_index),
        ).fetchone()

    if not row or not row["sentences_json"]:
        return fallback_sentence, fallback_sentence

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
) -> dict[str, Any]:
    """Ask a grammar/nuance question about a sentence and persist the thread."""
    clean_target = (target_text or "").strip()
    resolved_sentence, context_text = _load_sentence_context(
        book_id, chapter_index, sent_id, fallback_sentence=sentence_text
    )
    if not resolved_sentence:
        resolved_sentence = sentence_text.strip()
        context_text = resolved_sentence

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

    # If the user clicked ? without typing a follow-up and a thread already exists, return it
    if existing and not clean_question and prior_messages:
        return {
            "llm_unavailable": False,
            "reused": True,
            "thread": _row_to_thread(existing),
        }

    if not prior_messages:
        focus_line = f"特に気になる箇所: 「{clean_target}」\n" if clean_target else ""
        ask_line = (
            clean_question
            if clean_question
            else "この文の文法構造・使われている表現・ニュアンスを分かりやすく解説してください。"
        )
        display_user_msg = clean_question or (
            f"「{clean_target}」の意味・文法を教えて"
            if clean_target
            else "この文の文法とニュアンスを解説して"
        )
        llm_prompt = (
            f"【前後の文脈】\n{context_text}\n\n"
            f"【対象の文】\n{resolved_sentence}\n"
            f"{focus_line}\n"
            f"【質問】\n{ask_line}"
        )
        llm_messages = [{"role": "user", "content": llm_prompt}]
    else:
        display_user_msg = clean_question
        # Rebuild full context on turn 1 so follow-ups retain passage awareness
        first_user = (
            f"【前後の文脈】\n{context_text}\n\n"
            f"【対象の文】\n{resolved_sentence}\n"
            + (f"特に気になる箇所: 「{clean_target}」\n\n" if clean_target else "\n")
            + f"【質問】\n{prior_messages[0]['content']}"
        )
        llm_messages = [{"role": "user", "content": first_user}]
        for msg in prior_messages[1:]:
            llm_messages.append({"role": msg["role"], "content": msg["content"]})
        llm_messages.append({"role": "user", "content": display_user_msg})

    settings = db.get_all_settings()
    try:
        answer_text, provider, model = await llm.generate_text(
            settings=settings,
            system_prompt=_GRAMMAR_SYSTEM_PROMPT,
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
        {"role": "user", "content": display_user_msg, "created_at": now},
        {
            "role": "assistant",
            "content": answer_text,
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
