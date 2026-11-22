"""Zero-backlog adaptive mini-game generator and session scoring service."""

from __future__ import annotations

import json
import random
from typing import Any

from app import db, dictionary
from app.services import books_service
from app.tokenizer import (
    chunk_into_bunsetsu,
    extract_chapter_vocab_pool,
    extract_particle_quiz,
)

_FALLBACK_DISTRACTORS = ["時間", "場所", "気持ち", "言葉", "歩く", "走る", "見る", "聞く"]


def _load_chapter_sentences(book_id: int, chapter_index: int) -> list[dict[str, str]]:
    """Ensure the chapter is tokenized and return its segmented sentences list."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT sentences_json FROM chapters WHERE book_id = ? AND chapter_index = ?",
            (book_id, chapter_index),
        ).fetchone()
    if row is None:
        return []
    if not row["sentences_json"]:
        books_service.get_chapter(book_id, chapter_index)
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT sentences_json FROM chapters WHERE book_id = ? AND chapter_index = ?",
                (book_id, chapter_index),
            ).fetchone()
    if not row or not row["sentences_json"]:
        return []
    return json.loads(row["sentences_json"])


def _lookup_surface_for_lemma(
    book_id: int, chapter_index: int, lemma: str, sent_id: str | None
) -> str | None:
    with db.get_conn() as conn:
        if sent_id:
            row = conn.execute(
                """
                SELECT surface FROM lookup_events
                WHERE book_id = ? AND chapter_index = ? AND lemma = ? AND sent_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (book_id, chapter_index, lemma, sent_id),
            ).fetchone()
            if row:
                return row["surface"]
        row = conn.execute(
            """
            SELECT surface FROM lookup_events
            WHERE book_id = ? AND lemma = ?
            ORDER BY id DESC LIMIT 1
            """,
            (book_id, lemma),
        ).fetchone()
    return row["surface"] if row else None


def _get_word_hint_and_explanation(lemma: str, reading: str) -> tuple[str, str]:
    """Fetch cached J-J summary or offline JMdict short gloss for quiz feedback."""
    hint = f"読み: {reading}" if reading else ""
    explanation_parts: list[str] = []

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT jj_json, en_json FROM dict_cache WHERE lemma = ? AND reading = ?",
            (lemma, reading),
        ).fetchone()

    if row and row["jj_json"]:
        jj = json.loads(row["jj_json"])
        if jj.get("simple_definition"):
            hint = jj["simple_definition"]
            explanation_parts.append(jj["simple_definition"])

    en_data = json.loads(row["en_json"]) if (row and row["en_json"]) else None
    if en_data is None:
        en_data = dictionary.lookup_english(lemma=lemma, surface=lemma, reading=reading)
    if en_data and en_data.get("entries"):
        glosses = en_data["entries"][0].get("senses", [{}])[0].get("glosses", [])
        if glosses:
            explanation_parts.append(f"EN: {'; '.join(glosses[:3])}")

    head = f"{lemma}（{reading}）" if reading and reading != lemma else lemma
    explanation = f"{head} — {' / '.join(explanation_parts)}" if explanation_parts else head
    return hint, explanation


def _build_cloze_question(
    *,
    q_idx: int,
    lemma: str,
    reading: str,
    surface_candidates: list[str],
    preferred_sentence: str,
    sentences: list[dict[str, str]],
    vocab_pool: list[dict[str, Any]],
    trouble_item_id: int | None,
    fallback: bool = False,
    avoid_sentences: set[str] | None = None,
    avoid_kind_sentences: set[tuple[str, str]] | None = None,
) -> dict[str, Any] | None:
    """Create a 4-choice Context Cloze question for a vocabulary item."""
    target_sentence = ""
    matched_surface = ""

    candidate_sentences: list[str] = []
    if preferred_sentence:
        candidate_sentences.append(preferred_sentence)
    for s in sentences:
        txt = s.get("text", "")
        if txt and txt not in candidate_sentences:
            candidate_sentences.append(txt)

    if avoid_sentences:
        candidate_sentences.sort(key=lambda st: (1 if st in avoid_sentences else 0))

    blocked_cloze = {st for k, st in (avoid_kind_sentences or set()) if k == "cloze"}
    for sent in candidate_sentences:
        if sent in blocked_cloze:
            continue
        for cand in surface_candidates:
            if cand and cand in sent:
                target_sentence = sent
                matched_surface = cand
                break
        if target_sentence:
            break

    if not target_sentence or not matched_surface:
        return None

    blanked = target_sentence.replace(matched_surface, "＿＿＿", 1)

    # Pick 3 distractors from chapter vocab pool (prefer matching surface/lemma style)
    target_pos = next((v["pos"] for v in vocab_pool if v["lemma"] == lemma), None)
    distractors: list[str] = []
    seen = {matched_surface, lemma}

    ordered_pool = sorted(
        vocab_pool,
        key=lambda v: (0 if target_pos and v["pos"] == target_pos else 1, v["surface"]),
    )
    for v in ordered_pool:
        cand = v["surface"] if matched_surface != lemma else v["lemma"]
        if cand not in seen and len(cand) >= 2:
            seen.add(cand)
            distractors.append(cand)
        if len(distractors) >= 3:
            break

    for pad in _FALLBACK_DISTRACTORS:
        if len(distractors) >= 3:
            break
        if pad not in seen:
            seen.add(pad)
            distractors.append(pad)

    choices = [matched_surface, *distractors[:3]]
    rng = random.Random(target_sentence + matched_surface)
    rng.shuffle(choices)

    hint, explanation = _get_word_hint_and_explanation(lemma, reading)
    return {
        "question_id": f"q_{q_idx}",
        "kind": "cloze",
        "trouble_item_id": trouble_item_id,
        "prompt_label": "Context Cloze",
        "target_display": lemma,
        "blanked_sentence": blanked,
        "choices": choices,
        "correct_answer": matched_surface,
        "full_sentence": target_sentence,
        "hint": hint,
        "explanation": explanation,
        "fallback": fallback,
    }


def _build_scramble_question(
    *,
    q_idx: int,
    sentence: str,
    target_display: str,
    trouble_item_id: int | None,
    fallback: bool = False,
) -> dict[str, Any] | None:
    """Create a Bunsetsu Chunk Scramble question from a sentence."""
    chunks = chunk_into_bunsetsu(sentence)
    if len(chunks) < 3 or len(chunks) > 7:
        return None

    shuffled = list(chunks)
    rng = random.Random(sentence + f"#{q_idx}")
    rng.shuffle(shuffled)
    if shuffled == chunks:
        shuffled = shuffled[1:] + shuffled[:1]

    hint = f"Focus: {target_display}" if target_display else "Arrange the chunks into natural Japanese order."
    return {
        "question_id": f"q_{q_idx}",
        "kind": "scramble",
        "trouble_item_id": trouble_item_id,
        "prompt_label": "Bunsetsu Scramble",
        "target_display": target_display or chunks[0],
        "chunks": shuffled,
        "correct_answer": chunks,
        "full_sentence": "".join(chunks),
        "hint": hint,
        "explanation": sentence.strip(),
        "fallback": fallback,
    }


def _build_particle_question(
    *,
    q_idx: int,
    sentence: str,
    target_display: str,
    trouble_item_id: int | None,
    fallback: bool = False,
) -> dict[str, Any] | None:
    """Create a 4-choice Particle Fix question from a sentence."""
    parsed = extract_particle_quiz(sentence)
    if parsed is None:
        return None

    return {
        "question_id": f"q_{q_idx}",
        "kind": "particle",
        "trouble_item_id": trouble_item_id,
        "prompt_label": "Particle Fix",
        "target_display": target_display or parsed["correct"],
        "blanked_sentence": parsed["blanked_sentence"],
        "choices": parsed["choices"],
        "correct_answer": parsed["correct"],
        "full_sentence": parsed["full_sentence"],
        "hint": f"Focus: {target_display}" if target_display else "Pick the particle that fits the sentence.",
        "explanation": parsed["full_sentence"],
        "fallback": fallback,
    }


def generate_quiz(
    *,
    book_id: int,
    chapter_index: int,
    trigger_type: str = "chapter",
    max_questions: int = 5,
) -> dict[str, Any]:
    """Build an adaptive mini-game round mixing Cloze, Bunsetsu Scramble, and Particle Fix."""
    sentences = _load_chapter_sentences(book_id, chapter_index)
    vocab_pool = extract_chapter_vocab_pool(sentences)

    with db.get_conn() as conn:
        book_row = conn.execute("SELECT title FROM books WHERE id = ?", (book_id,)).fetchone()
        ch_row = conn.execute(
            "SELECT title FROM chapters WHERE book_id = ? AND chapter_index = ?",
            (book_id, chapter_index),
        ).fetchone()
        trouble_rows = conn.execute(
            """
            SELECT * FROM trouble_items
            WHERE retired = 0 AND (book_id = ? OR book_id IS NULL)
            ORDER BY
                CASE WHEN last_chapter_index = ? THEN 0 ELSE 1 END ASC,
                trouble_score DESC,
                updated_at DESC
            LIMIT 30
            """,
            (book_id, chapter_index),
        ).fetchall()

    questions: list[dict[str, Any]] = []
    used_sentences: set[str] = set()
    used_kind_sentences: set[tuple[str, str]] = set()
    used_trouble_ids: set[int] = set()
    grammar_toggle = 0

    for row in trouble_rows:
        if len(questions) >= max_questions:
            break
        item = dict(row)
        item_id = int(item["id"])
        q: dict[str, Any] | None = None

        if item["item_type"] == "grammar":
            sent = (item.get("sent_text") or "").strip()
            if not sent:
                continue
            builders = (
                (_build_scramble_question, _build_particle_question)
                if grammar_toggle % 2 == 0
                else (_build_particle_question, _build_scramble_question)
            )
            for builder in builders:
                candidate = builder(
                    q_idx=len(questions),
                    sentence=sent,
                    target_display=item["display_text"],
                    trouble_item_id=item_id,
                )
                if candidate is not None and (candidate["kind"], candidate["full_sentence"]) not in used_kind_sentences:
                    q = candidate
                    break
            grammar_toggle += 1
        else:
            lemma = item["display_text"]
            reading = item.get("reading") or ""
            surf = _lookup_surface_for_lemma(book_id, chapter_index, lemma, item.get("sent_id"))
            candidates = [c for c in (surf, lemma) if c]
            pref_sent = (item.get("sent_text") or "").strip()
            q = _build_cloze_question(
                q_idx=len(questions),
                lemma=lemma,
                reading=reading,
                surface_candidates=candidates,
                preferred_sentence=pref_sent,
                sentences=sentences,
                vocab_pool=vocab_pool,
                trouble_item_id=item_id,
                avoid_sentences=used_sentences,
                avoid_kind_sentences=used_kind_sentences,
            )

        if q is not None:
            questions.append(q)
            used_sentences.add(q["full_sentence"])
            used_kind_sentences.add((q["kind"], q["full_sentence"]))
            used_trouble_ids.add(item_id)

    # Top up with diverse chapter-based mechanics if trouble items < max_questions
    if len(questions) < max_questions and sentences:
        have_kinds = {q["kind"] for q in questions}
        # Prefer adding missing mechanic types first (particle / scramble / cloze)
        for sent_obj in sentences:
            if len(questions) >= max_questions:
                break
            sent_txt = (sent_obj.get("text") or "").strip()
            if not sent_txt or sent_txt in used_sentences:
                continue

            q = None
            if "particle" not in have_kinds:
                q = _build_particle_question(
                    q_idx=len(questions),
                    sentence=sent_txt,
                    target_display="",
                    trouble_item_id=None,
                    fallback=True,
                )
            elif "scramble" not in have_kinds:
                q = _build_scramble_question(
                    q_idx=len(questions),
                    sentence=sent_txt,
                    target_display="",
                    trouble_item_id=None,
                    fallback=True,
                )

            if q is not None:
                questions.append(q)
                used_sentences.add(q["full_sentence"])
                have_kinds.add(q["kind"])

        for v in vocab_pool:
            if len(questions) >= max_questions:
                break
            if v["sentence_text"] in used_sentences:
                continue
            q = _build_cloze_question(
                q_idx=len(questions),
                lemma=v["lemma"],
                reading=v["reading"],
                surface_candidates=[v["surface"], v["lemma"]],
                preferred_sentence=v["sentence_text"],
                sentences=sentences,
                vocab_pool=vocab_pool,
                trouble_item_id=None,
                fallback=True,
            )
            if q is not None:
                questions.append(q)
                used_sentences.add(q["full_sentence"])

        # Final sweep using particle/scramble on remaining sentences
        for sent_obj in sentences:
            if len(questions) >= max_questions:
                break
            sent_txt = (sent_obj.get("text") or "").strip()
            if not sent_txt or sent_txt in used_sentences:
                continue
            q = _build_particle_question(
                q_idx=len(questions),
                sentence=sent_txt,
                target_display="",
                trouble_item_id=None,
                fallback=True,
            ) or _build_scramble_question(
                q_idx=len(questions),
                sentence=sent_txt,
                target_display="",
                trouble_item_id=None,
                fallback=True,
            )
            if q is not None:
                questions.append(q)
                used_sentences.add(q["full_sentence"])

    # Normalize question IDs sequentially
    for idx, q in enumerate(questions):
        q["question_id"] = f"q_{idx}"

    return {
        "book_id": book_id,
        "book_title": book_row["title"] if book_row else "",
        "chapter_index": chapter_index,
        "chapter_title": ch_row["title"] if ch_row else "",
        "trigger_type": trigger_type,
        "trouble_based_count": len(used_trouble_ids),
        "questions": questions,
    }


def submit_quiz(
    *,
    book_id: int | None,
    chapter_index: int | None,
    trigger_type: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Score a completed quiz round, update trouble items, and save quiz history."""
    conquered: list[dict[str, Any]] = []
    still_tricky: list[dict[str, Any]] = []
    correct_count = 0

    for item in results:
        is_correct = bool(item.get("correct"))
        if is_correct:
            correct_count += 1
        trouble_id = item.get("trouble_item_id")
        if trouble_id is not None:
            updated = db.apply_quiz_item_result(int(trouble_id), is_correct)
            if updated is not None:
                summary = {
                    "id": updated["id"],
                    "display_text": updated["display_text"],
                    "item_type": updated["item_type"],
                    "trouble_score": updated["trouble_score"],
                    "retired": bool(updated["retired"]),
                }
                if updated["retired"] or is_correct:
                    conquered.append(summary)
                else:
                    still_tricky.append(summary)

    now = db.utc_now_iso()
    with db.get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO quiz_sessions
                (book_id, chapter_index, trigger_type, total_questions, correct_count, questions_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id,
                chapter_index,
                trigger_type,
                len(results),
                correct_count,
                json.dumps(results, ensure_ascii=False),
                now,
            ),
        )
        session_id = int(cur.lastrowid)

    return {
        "session_id": session_id,
        "score": correct_count,
        "total": len(results),
        "conquered": conquered,
        "still_tricky": still_tricky,
    }


def list_quiz_history(book_id: int | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Return recent completed quiz sessions."""
    with db.get_conn() as conn:
        if book_id is not None:
            rows = conn.execute(
                """
                SELECT id, book_id, chapter_index, trigger_type, total_questions, correct_count, created_at
                FROM quiz_sessions
                WHERE book_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (book_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, book_id, chapter_index, trigger_type, total_questions, correct_count, created_at
                FROM quiz_sessions
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]
