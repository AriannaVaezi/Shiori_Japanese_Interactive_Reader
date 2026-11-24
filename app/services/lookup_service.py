"""Two-step vocabulary lookup service: easy Japanese (J-J) first, then offline English."""

from __future__ import annotations

import json
from typing import Any

from app import db, dictionary, llm, tokenizer

_JJ_SYSTEM_PROMPT = """あなたは日本語学習者向けのやさしい国語辞典です。
英語は一切使わず、小学生〜初中級の学習者が読める簡単な日本語だけで語句を説明してください。

必ず次のJSON形式のみを返してください（説明文やマークダウンは不要）:
{
  "simple_definition": "やさしい日本語での短い意味（1〜2文、英語禁止）",
  "part_of_speech": "品詞（例：動詞・名詞・形容詞・副詞・慣用句・助詞）",
  "context_note": "小説の文の中での使われ方やニュアンス（1文、やさしい日本語）",
  "examples": [
    {"jp": "短い日常の例文1", "reading": "ひらがなの読みヒント（または大事な漢字の読み）"},
    {"jp": "短い日常の例文2", "reading": "ひらがなの読みヒント"}
  ]
}"""


_JJ_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "simple_definition": {"type": "string", "minLength": 1},
        "part_of_speech": {"type": "string"},
        "context_note": {"type": "string"},
        "examples": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "jp": {"type": "string"},
                    "reading": {"type": "string"},
                },
                "required": ["jp", "reading"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["simple_definition", "part_of_speech", "context_note", "examples"],
    "additionalProperties": False,
}

_DEF_KEYS = (
    "simple_definition",
    "definition",
    "meaning",
    "simple_meaning",
    "explanation",
    "意味",
    "簡単な意味",
    "やさしい意味",
    "定義",
    "説明",
)
_POS_KEYS = ("part_of_speech", "pos", "品詞")
_NOTE_KEYS = ("context_note", "nuance", "note", "文脈", "ニュアンス")
_EXAMPLES_KEYS = ("examples", "example_sentences", "例文")


def _pick_first_str(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    for k in keys:
        val = mapping.get(k)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _unwrap_jj_source(raw: dict[str, Any]) -> dict[str, Any]:
    if _pick_first_str(raw, _DEF_KEYS) or _pick_first_str(raw, _NOTE_KEYS):
        return raw
    for val in raw.values():
        if isinstance(val, dict) and (
            _pick_first_str(val, _DEF_KEYS) or _pick_first_str(val, _NOTE_KEYS)
        ):
            return val
    return raw


def _normalize_jj_payload(raw: dict[str, Any]) -> dict[str, Any]:
    src = _unwrap_jj_source(raw)
    examples_raw: Any = []
    for k in _EXAMPLES_KEYS:
        if src.get(k):
            examples_raw = src[k]
            break

    examples: list[dict[str, str]] = []
    if isinstance(examples_raw, list):
        for item in examples_raw[:2]:
            if isinstance(item, dict):
                jp = _pick_first_str(item, ("jp", "sentence", "text", "例文"))
                if jp:
                    examples.append(
                        {
                            "jp": jp,
                            "reading": _pick_first_str(
                                item, ("reading", "kana", "furigana", "読み")
                            ),
                        }
                    )
            elif isinstance(item, str) and item.strip():
                examples.append({"jp": item.strip(), "reading": ""})

    context_note = _pick_first_str(src, _NOTE_KEYS)
    simple_definition = _pick_first_str(src, _DEF_KEYS) or context_note

    return {
        "simple_definition": simple_definition,
        "part_of_speech": _pick_first_str(src, _POS_KEYS),
        "context_note": context_note,
        "examples": examples,
    }


def _log_lookup_event(
    *,
    book_id: int | None,
    chapter_index: int | None,
    sent_id: str | None,
    lemma: str,
    surface: str,
    reading: str,
    revealed_english: bool,
    sentence_text: str = "",
) -> None:
    now = db.utc_now_iso()
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO lookup_events
                (book_id, chapter_index, sent_id, lemma, surface, reading, revealed_english, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id,
                chapter_index,
                sent_id,
                lemma,
                surface,
                reading,
                1 if revealed_english else 0,
                now,
            ),
        )

    item_key = f"{lemma}|{reading}"
    db.record_trouble_event(
        item_type="word",
        item_key=item_key,
        display_text=lemma or surface,
        reading=reading,
        book_id=book_id,
        chapter_index=chapter_index,
        sent_id=sent_id,
        sent_text=sentence_text or None,
        jj_delta=0 if revealed_english else 1,
        en_delta=1 if revealed_english else 0,
        score_delta=2.0 if revealed_english else 0.5,
    )


def _resolve_reading_for_text(text: str) -> str:
    """Resolve a canonical hiragana reading for an arbitrary selected span."""
    clean = (text or "").strip()
    if not clean:
        return ""
    exact = dictionary.lookup_english_exact(lemma=clean)
    if exact.get("all_valid_readings"):
        return str(exact["all_valid_readings"][0]).strip()
    return tokenizer.text_to_hiragana_reading(clean)


async def lookup_jj(
    *,
    lemma: str,
    reading: str,
    surface: str = "",
    pos: str = "",
    sentence_text: str = "",
    book_id: int | None = None,
    chapter_index: int | None = None,
    sent_id: str | None = None,
    force_regenerate: bool = False,
) -> dict[str, Any]:
    """Return a cached or freshly generated easy-Japanese definition for a word."""
    clean_lemma = (lemma or surface).strip()
    clean_reading = (reading or "").strip() or _resolve_reading_for_text(clean_lemma) or clean_lemma
    clean_surface = (surface or clean_lemma).strip()

    if not force_regenerate:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT jj_json, provider, model FROM dict_cache WHERE lemma = ? AND reading = ?",
                (clean_lemma, clean_reading),
            ).fetchone()
        if row and row["jj_json"]:
            jj_data = json.loads(row["jj_json"])
            _log_lookup_event(
                book_id=book_id,
                chapter_index=chapter_index,
                sent_id=sent_id,
                lemma=clean_lemma,
                surface=clean_surface,
                reading=clean_reading,
                revealed_english=False,
                sentence_text=sentence_text,
            )
            return {
                "cached": True,
                "llm_unavailable": False,
                "lemma": clean_lemma,
                "reading": clean_reading,
                "surface": clean_surface,
                "provider": row["provider"],
                "model": row["model"],
                "jj": jj_data,
            }

    user_prompt = (
        f"見出し語（辞書形）: {clean_lemma}\n"
        f"読み: {clean_reading}\n"
        f"本文中の形: {clean_surface}\n"
        f"品詞ヒント: {pos or '不明'}\n"
        f"登場した文: {sentence_text or 'なし'}\n\n"
        "この語句をやさしい日本語で説明してください。"
    )

    settings = db.get_all_settings()
    try:
        jj_data: dict[str, Any] | None = None
        provider = ""
        model = ""
        last_val_err: ValueError | None = None
        for attempt in range(2):
            prompt = user_prompt
            if attempt == 1:
                prompt = (
                    f"{user_prompt}\n\n"
                    '注意: 前回のJSONに "simple_definition" がありませんでした。'
                    '必ず "simple_definition" キーに1〜2文のやさしい日本語の意味を入れてください。'
                )
            try:
                raw_text, provider, model = await llm.generate_text(
                    settings=settings,
                    system_prompt=_JJ_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    expect_json=True,
                    json_schema=_JJ_JSON_SCHEMA,
                )
                parsed = llm.extract_json_object(raw_text)
                candidate = _normalize_jj_payload(parsed)
                if not candidate["simple_definition"]:
                    raise ValueError("Missing simple_definition in LLM JSON output.")
                jj_data = candidate
                break
            except ValueError as err:
                last_val_err = err
        if jj_data is None:
            raise last_val_err or ValueError("Missing simple_definition in LLM JSON output.")
    except (llm.LLMError, ValueError) as exc:
        return {
            "cached": False,
            "llm_unavailable": True,
            "error": str(exc),
            "lemma": clean_lemma,
            "reading": clean_reading,
            "surface": clean_surface,
            "jj": None,
        }

    now = db.utc_now_iso()
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO dict_cache (lemma, reading, jj_json, provider, model, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(lemma, reading) DO UPDATE SET
                jj_json = excluded.jj_json,
                provider = excluded.provider,
                model = excluded.model,
                updated_at = excluded.updated_at
            """,
            (
                clean_lemma,
                clean_reading,
                json.dumps(jj_data, ensure_ascii=False),
                provider,
                model,
                now,
            ),
        )

    _log_lookup_event(
        book_id=book_id,
        chapter_index=chapter_index,
        sent_id=sent_id,
        lemma=clean_lemma,
        surface=clean_surface,
        reading=clean_reading,
        revealed_english=False,
        sentence_text=sentence_text,
    )

    return {
        "cached": False,
        "llm_unavailable": False,
        "lemma": clean_lemma,
        "reading": clean_reading,
        "surface": clean_surface,
        "provider": provider,
        "model": model,
        "jj": jj_data,
    }


def lookup_en(
    *,
    lemma: str,
    norm_form: str = "",
    surface: str = "",
    reading: str = "",
    book_id: int | None = None,
    chapter_index: int | None = None,
    sent_id: str | None = None,
    sentence_text: str = "",
) -> dict[str, Any]:
    """Return offline JMdict English entries and record an English-reveal trouble signal."""
    clean_lemma = (lemma or surface).strip()
    clean_reading = (reading or "").strip() or _resolve_reading_for_text(clean_lemma) or clean_lemma
    clean_surface = (surface or clean_lemma).strip()

    en_data: dict[str, Any] | None = None
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT en_json FROM dict_cache WHERE lemma = ? AND reading = ?",
            (clean_lemma, clean_reading),
        ).fetchone()
    if row and row["en_json"]:
        en_data = json.loads(row["en_json"])
    else:
        en_data = dictionary.lookup_english(
            lemma=clean_lemma,
            norm_form=norm_form,
            surface=clean_surface,
            reading=clean_reading,
        )
        if not en_data.get("entries") and not en_data.get("missing_db"):
            fallback_lemma = tokenizer.lemmatize_single_word(clean_lemma)
            if fallback_lemma:
                en_data = dictionary.lookup_english(
                    lemma=fallback_lemma,
                    surface=clean_surface,
                )
        if not en_data.get("missing_db"):
            now = db.utc_now_iso()
            with db.get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO dict_cache (lemma, reading, en_json, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(lemma, reading) DO UPDATE SET
                        en_json = excluded.en_json,
                        updated_at = excluded.updated_at
                    """,
                    (clean_lemma, clean_reading, json.dumps(en_data, ensure_ascii=False), now),
                )

    _log_lookup_event(
        book_id=book_id,
        chapter_index=chapter_index,
        sent_id=sent_id,
        lemma=clean_lemma,
        surface=clean_surface,
        reading=clean_reading,
        revealed_english=True,
        sentence_text=sentence_text,
    )

    return {
        "lemma": clean_lemma,
        "reading": clean_reading,
        "surface": clean_surface,
        **(en_data or {"query": clean_lemma, "entries": []}),
    }


def list_trouble_items(
    *,
    book_id: int | None = None,
    include_retired: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return tracked trouble words and grammar items ordered by trouble_score."""
    clauses = []
    params: list[Any] = []
    if book_id is not None:
        clauses.append("book_id = ?")
        params.append(book_id)
    if not include_retired:
        clauses.append("retired = 0")

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    with db.get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM trouble_items
            {where_sql}
            ORDER BY retired ASC, trouble_score DESC, updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def set_trouble_retired(item_id: int, retired: bool) -> dict[str, Any] | None:
    """Mark a trouble item as known (retired) or restore it to active review."""
    return db.set_trouble_retired(item_id, retired)
