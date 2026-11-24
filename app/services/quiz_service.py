"""Zero-backlog adaptive mini-game generator and session scoring service."""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone
from typing import Any

from app import db, dictionary
from app.services import books_service
from app.tokenizer import (
    chunk_into_bunsetsu,
    extract_chapter_vocab_pool,
    extract_particle_quiz,
    find_exact_word_span,
    katakana_to_hiragana,
)

_KANJI_RE = re.compile(r"[\u4e00-\u9fff]")

_FALLBACK_DISTRACTORS = ["時間", "場所", "気持ち", "言葉", "歩く", "走る", "見る", "聞く"]

_FALLBACK_VOCAB_ENTRIES: list[tuple[str, str, str]] = [
    ("約束", "やくそく", "promise; agreement"),
    ("秘密", "ひみつ", "secret; privacy"),
    ("勇気", "ゆうき", "courage; bravery"),
    ("風景", "ふうけい", "scenery; landscape"),
    ("記憶", "きおく", "memory; recollection"),
    ("準備", "じゅんび", "preparation; arrangements"),
    ("冒険", "ぼうけん", "adventure; venture"),
    ("不思議", "ふしぎ", "mystery; wonder"),
]

_DAKUTEN_SWAPS = {
    "か": "が", "が": "か", "き": "ぎ", "ぎ": "き", "く": "ぐ", "ぐ": "く",
    "け": "げ", "げ": "け", "こ": "ご", "ご": "こ",
    "さ": "ざ", "ざ": "さ", "し": "じ", "じ": "し", "す": "ず", "ず": "す",
    "せ": "ぜ", "ぜ": "せ", "そ": "ぞ", "ぞ": "そ",
    "た": "だ", "だ": "た", "ち": "ぢ", "て": "で", "で": "て", "と": "ど", "ど": "と",
    "は": "ば", "ば": "は", "ひ": "び", "び": "ひ", "ふ": "ぶ", "ぶ": "ふ",
    "へ": "べ", "べ": "へ", "ほ": "ぼ", "ぼ": "ほ",
}


def _load_chapter_sentences(book_id: int, chapter_index: int) -> list[dict[str, str]]:
    """Ensure the chapter is tokenized and return its segmented sentences list (or manga OCR blocks)."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT sentences_json FROM chapters WHERE book_id = ? AND chapter_index = ?",
            (book_id, chapter_index),
        ).fetchone()
    if row is None:
        with db.get_conn() as conn:
            manga_rows = conn.execute(
                """
                SELECT page_index, ocr_json FROM manga_pages
                WHERE book_id = ? AND ocr_json IS NOT NULL
                ORDER BY ABS(page_index - ?) ASC, page_index ASC
                LIMIT 15
                """,
                (book_id, chapter_index),
            ).fetchall()
        manga_sentences: list[dict[str, str]] = []
        for mr in manga_rows:
            try:
                ocr_data = json.loads(mr["ocr_json"])
            except Exception:
                continue
            for blk in ocr_data.get("blocks") or []:
                blk_text = (blk.get("text") or "").strip()
                blk_id = (blk.get("block_id") or "").strip()
                if blk_text and blk_id:
                    manga_sentences.append({"sent_id": blk_id, "text": blk_text})
        return manga_sentences
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


def _short_en_gloss(en_data: dict[str, Any] | None) -> str | None:
    if not en_data or not en_data.get("entries"):
        return None
    senses = en_data["entries"][0].get("senses") or []
    for sense in senses:
        glosses = [g.strip() for g in (sense.get("glosses") or []) if g and g.strip()]
        if not glosses:
            continue
        joined = "; ".join(glosses[:2])
        if len(joined) > 75:
            joined = glosses[0][:72].rstrip() + ("…" if len(glosses[0]) > 72 else "")
        return joined
    return None


def _gloss_terms(gloss_text: str) -> set[str]:
    return {part.strip().lower() for part in (gloss_text or "").split(";") if part.strip()}


def _glosses_overlap(
    glosses_a: set[str],
    glosses_b: set[str],
    gloss_a: str,
    gloss_b: str,
) -> bool:
    norm_a = (gloss_a or "").strip().lower()
    norm_b = (gloss_b or "").strip().lower()
    if norm_a and norm_b and norm_a == norm_b:
        return True
    terms_a = glosses_a | _gloss_terms(gloss_a)
    terms_b = glosses_b | _gloss_terms(gloss_b)
    return bool(terms_a & terms_b)


def _get_word_meta(lemma: str, reading: str) -> dict[str, Any]:
    """Fetch strictly JMdict-backed English glosses and valid readings."""
    en_data = dictionary.lookup_english_exact(lemma=lemma, reading=reading)
    en_gloss = _short_en_gloss(en_data)
    all_glosses = set(en_data.get("all_glosses") or [])
    all_valid_readings = list(en_data.get("all_valid_readings") or [])

    head = f"{lemma}（{reading}）" if reading and reading != lemma else lemma
    explanation = f"{head} — {en_gloss}" if en_gloss else head
    return {
        "en_gloss": en_gloss,
        "all_glosses": all_glosses,
        "all_valid_readings": all_valid_readings,
        "missing_db": bool(en_data.get("missing_db")),
        "explanation": explanation,
    }


def _verified_reading_meta(lemma: str, reading: str) -> tuple[str, dict[str, Any]] | None:
    """Validate that `lemma` is a kanji word whose `reading` matches JMdict."""
    norm_reading = katakana_to_hiragana((reading or "").strip())
    if not lemma or not norm_reading or len(norm_reading) < 2 or norm_reading == lemma:
        return None
    if not _KANJI_RE.search(lemma):
        return None

    meta = _get_word_meta(lemma, norm_reading)
    if not meta["all_valid_readings"]:
        meta = _get_word_meta(lemma, "")
    if not meta["all_valid_readings"] or norm_reading not in meta["all_valid_readings"]:
        return None
    return norm_reading, meta


def _generate_reading_distractors(
    reading: str,
    vocab_pool: list[dict[str, Any]],
    forbidden_readings: set[str] | None = None,
) -> list[str]:
    """Generate 3 plausible hiragana distractors excluding any valid readings of the word."""
    distractors: list[str] = []
    seen = {reading} | (forbidden_readings or set())

    # 1. Realistic phonetic variations (dakuten / long vowel / sokuon)
    for idx, ch in enumerate(reading):
        swapped = _DAKUTEN_SWAPS.get(ch)
        if swapped:
            cand = reading[:idx] + swapped + reading[idx + 1 :]
            if cand not in seen:
                seen.add(cand)
                distractors.append(cand)
            if len(distractors) >= 3:
                return distractors

    for old, new in (
        ("しょう", "しょ"),
        ("じょう", "じょ"),
        ("ちょう", "ちょ"),
        ("おう", "お"),
        ("えい", "え"),
        ("ゅう", "ゅ"),
        ("っ", ""),
    ):
        if old in reading:
            cand = reading.replace(old, new, 1)
            if len(cand) >= 2 and cand not in seen:
                seen.add(cand)
                distractors.append(cand)
            if len(distractors) >= 3:
                return distractors

    # 2. Similar-length readings from chapter vocab pool
    sorted_pool = sorted(
        vocab_pool,
        key=lambda v: abs(len(v.get("reading") or "") - len(reading)),
    )
    for v in sorted_pool:
        cand = (v.get("reading") or "").strip()
        if len(cand) >= 2 and cand not in seen:
            seen.add(cand)
            distractors.append(cand)
        if len(distractors) >= 3:
            return distractors

    # 3. Safe fallback readings
    for _, fb_read, _ in _FALLBACK_VOCAB_ENTRIES:
        if fb_read not in seen:
            seen.add(fb_read)
            distractors.append(fb_read)
        if len(distractors) >= 3:
            break

    return distractors[:3]


def _build_reading_recall_question(
    *,
    q_idx: int,
    lemma: str,
    reading: str,
    vocab_pool: list[dict[str, Any]],
    trouble_item_id: int | None,
    fallback: bool = False,
) -> dict[str, Any] | None:
    """Create a Kanji -> Hiragana reading recall question validated against JMdict."""
    verified = _verified_reading_meta(lemma, reading)
    if verified is None:
        return None
    norm_reading, meta = verified

    distractors = _generate_reading_distractors(
        norm_reading,
        vocab_pool,
        forbidden_readings=set(meta["all_valid_readings"]),
    )
    if len(distractors) < 3:
        return None

    choices = [norm_reading, *distractors[:3]]
    rng = random.Random(f"read:{lemma}:{norm_reading}")
    rng.shuffle(choices)

    hint = meta["en_gloss"] or ""
    return {
        "question_id": f"q_{q_idx}",
        "kind": "reading_recall",
        "trouble_item_id": trouble_item_id,
        "prompt_label": "Kanji → Reading",
        "target_display": lemma,
        "prompt_word": lemma,
        "prompt_text": "How do you read this word?",
        "choices": choices,
        "correct_answer": norm_reading,
        "full_sentence": f"{lemma}（{norm_reading}）",
        "hint": hint,
        "explanation": meta["explanation"],
        "fallback": fallback,
    }


def _collect_en_distractor_words(
    *,
    exclude_lemma: str,
    exclude_gloss: str,
    vocab_pool: list[dict[str, Any]],
    exclude_glosses: set[str] | None = None,
    limit: int = 3,
) -> list[dict[str, str]]:
    """Collect distinct words whose JMdict glosses do not overlap target or each other."""
    out: list[dict[str, str]] = []
    seen_lemmas = {exclude_lemma}
    used_gloss_sets: list[tuple[set[str], str]] = [
        (set(exclude_glosses or set()), exclude_gloss or "")
    ]

    def try_add(w: str, r: str, gloss: str, gset: set[str]) -> bool:
        if w in seen_lemmas or not gloss:
            return False
        if any(_glosses_overlap(gset, prev_set, gloss, prev_g) for prev_set, prev_g in used_gloss_sets):
            return False
        seen_lemmas.add(w)
        used_gloss_sets.append((gset, gloss))
        out.append({"lemma": w, "reading": r, "gloss": gloss})
        return True

    for v in vocab_pool[:30]:
        w = v["lemma"]
        r = v.get("reading") or ""
        if w in seen_lemmas:
            continue
        meta = _get_word_meta(w, r)
        if meta["en_gloss"]:
            try_add(w, r, meta["en_gloss"], meta["all_glosses"])
        if len(out) >= limit:
            return out

    for w, r, fb_gloss in _FALLBACK_VOCAB_ENTRIES:
        if len(out) >= limit:
            break
        meta = _get_word_meta(w, r)
        if meta["en_gloss"]:
            try_add(w, r, meta["en_gloss"], meta["all_glosses"])
        elif meta["missing_db"]:
            try_add(w, r, fb_gloss, {fb_gloss.lower()})

    return out


def _build_meaning_match_question(
    *,
    q_idx: int,
    lemma: str,
    reading: str,
    vocab_pool: list[dict[str, Any]],
    trouble_item_id: int | None,
    sent_text: str = "",
    fallback: bool = False,
) -> dict[str, Any] | None:
    """Create a standalone Word -> Dictionary Meaning question (JMdict only)."""
    meta = _get_word_meta(lemma, reading)
    correct_meaning = meta["en_gloss"]
    if not correct_meaning:
        return None

    dist_entries = _collect_en_distractor_words(
        exclude_lemma=lemma,
        exclude_gloss=correct_meaning,
        exclude_glosses=meta["all_glosses"],
        vocab_pool=vocab_pool,
        limit=3,
    )
    if len(dist_entries) < 3:
        return None
    distractor_meanings = [d["gloss"] for d in dist_entries]

    choices = [correct_meaning, *distractor_meanings[:3]]
    rng = random.Random(f"mean:{lemma}:{reading}")
    rng.shuffle(choices)

    hint = f"読み: {reading}" if reading else ""
    if sent_text:
        hint = f"{hint} · Context: {sent_text}" if hint else f"Context: {sent_text}"

    return {
        "question_id": f"q_{q_idx}",
        "kind": "meaning_match",
        "trouble_item_id": trouble_item_id,
        "prompt_label": "Word → Meaning",
        "target_display": lemma,
        "prompt_word": lemma,
        "prompt_text": "Choose the dictionary meaning:",
        "choices": choices,
        "correct_answer": correct_meaning,
        "full_sentence": f"{lemma}（{reading}）" if reading and reading != lemma else lemma,
        "hint": hint,
        "explanation": meta["explanation"],
        "fallback": fallback,
    }


def _build_def_to_word_question(
    *,
    q_idx: int,
    lemma: str,
    reading: str,
    vocab_pool: list[dict[str, Any]],
    trouble_item_id: int | None,
    fallback: bool = False,
) -> dict[str, Any] | None:
    """Create a reverse Dictionary Meaning -> Word recall question (JMdict only)."""
    meta = _get_word_meta(lemma, reading)
    prompt_def = meta["en_gloss"]
    if not prompt_def:
        return None

    dist_entries = _collect_en_distractor_words(
        exclude_lemma=lemma,
        exclude_gloss=prompt_def,
        exclude_glosses=meta["all_glosses"],
        vocab_pool=vocab_pool,
        limit=3,
    )
    if len(dist_entries) < 3:
        return None
    distractor_words = [d["lemma"] for d in dist_entries]

    choices = [lemma, *distractor_words[:3]]
    rng = random.Random(f"rev:{lemma}:{reading}")
    rng.shuffle(choices)

    hint = f"読みヒント: {reading[:1]}…（{len(reading)}文字）" if reading else ""
    return {
        "question_id": f"q_{q_idx}",
        "kind": "def_to_word",
        "trouble_item_id": trouble_item_id,
        "prompt_label": "Meaning → Word",
        "target_display": lemma,
        "prompt_text": prompt_def,
        "choices": choices,
        "correct_answer": lemma,
        "full_sentence": f"{lemma}（{reading}）" if reading and reading != lemma else lemma,
        "hint": hint,
        "explanation": meta["explanation"],
        "fallback": fallback,
    }


def _build_match_pairs_question(
    *,
    q_idx: int,
    candidate_words: list[dict[str, Any]],
    fallback: bool = False,
) -> dict[str, Any] | None:
    """Create a 4-pair Word <-> JMdict Meaning board with disjoint dictionary glosses."""
    chosen: list[dict[str, Any]] = []
    seen_lemmas: set[str] = set()

    pool_with_fallback = list(candidate_words) + [
        {"lemma": w, "reading": r, "trouble_item_id": None, "_fb_gloss": g}
        for w, r, g in _FALLBACK_VOCAB_ENTRIES
    ]

    for item in pool_with_fallback:
        lemma = (item.get("lemma") or "").strip()
        reading = (item.get("reading") or "").strip()
        if not lemma or lemma in seen_lemmas:
            continue
        meta = _get_word_meta(lemma, reading)
        en_gloss = meta["en_gloss"]
        gloss_set = meta["all_glosses"]
        if not en_gloss:
            if meta["missing_db"] and item.get("_fb_gloss"):
                en_gloss = str(item["_fb_gloss"])
                gloss_set = {en_gloss.lower()}
            else:
                continue

        if any(
            _glosses_overlap(gloss_set, prev["all_glosses"], en_gloss, prev["en_gloss"])
            for prev in chosen
        ):
            continue

        seen_lemmas.add(lemma)
        chosen.append(
            {
                "lemma": lemma,
                "reading": reading,
                "en_gloss": en_gloss,
                "all_glosses": gloss_set,
                "trouble_item_id": item.get("trouble_item_id"),
            }
        )
        if len(chosen) >= 4:
            break

    if len(chosen) < 4:
        return None

    pairs = []
    left_items = []
    right_items = []
    trouble_ids: list[int] = []

    for idx, entry in enumerate(chosen):
        meaning = entry["en_gloss"]
        pairs.append(
            {
                "id": idx,
                "word": entry["lemma"],
                "reading": entry["reading"],
                "meaning": meaning,
            }
        )
        left_items.append({"id": idx, "text": entry["lemma"]})
        right_items.append({"id": idx, "text": meaning})
        if entry["trouble_item_id"] is not None:
            trouble_ids.append(int(entry["trouble_item_id"]))

    rng_l = random.Random(f"matchL:{q_idx}:{pairs[0]['word']}")
    rng_r = random.Random(f"matchR:{q_idx}:{pairs[-1]['word']}")
    rng_l.shuffle(left_items)
    rng_r.shuffle(right_items)
    if [x["id"] for x in left_items] == [x["id"] for x in right_items]:
        right_items = right_items[1:] + right_items[:1]

    return {
        "question_id": f"q_{q_idx}",
        "kind": "match_pairs",
        "trouble_item_id": trouble_ids[0] if len(trouble_ids) == 1 else None,
        "trouble_item_ids": trouble_ids,
        "prompt_label": "Vocab Match-Up",
        "target_display": ", ".join(p["word"] for p in pairs),
        "prompt_text": "Match each Japanese word to its dictionary meaning:",
        "left_items": left_items,
        "right_items": right_items,
        "pairs": pairs,
        "correct_answer": 4,
        "full_sentence": " / ".join(
            f"{p['word']}（{p['reading']}）: {p['meaning']}" if p["reading"] else f"{p['word']}: {p['meaning']}"
            for p in pairs
        ),
        "hint": " / ".join(f"{p['word']}={p['reading']}" for p in pairs if p["reading"]),
        "explanation": "Tap a word on the left, then its matching meaning on the right.",
        "fallback": fallback,
    }


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
    """Create a 4-choice Text Context Cloze strictly matched on exact token boundaries."""
    meta = _get_word_meta(lemma, reading)
    blocked_cloze = {st for k, st in (avoid_kind_sentences or set()) if k == "cloze"}

    candidate_sentences: list[str] = []
    if preferred_sentence and preferred_sentence.strip():
        candidate_sentences.append(preferred_sentence.strip())
    for s in sentences:
        txt = (s.get("text") or "").strip()
        if txt and txt not in candidate_sentences:
            candidate_sentences.append(txt)

    if avoid_sentences:
        candidate_sentences.sort(key=lambda st: (1 if st in avoid_sentences else 0))

    target_sentence = ""
    matched_surface = ""
    blanked = ""

    for sent in candidate_sentences:
        if sent in blocked_cloze:
            continue
        span = find_exact_word_span(sent, lemma)
        if span is None:
            continue
        start, end, surf = span
        cand_blanked = f"{sent[:start]}＿＿＿{sent[end:]}"
        if cand_blanked.replace("＿＿＿", surf, 1) != sent:
            continue
        target_sentence = sent
        matched_surface = surf
        blanked = cand_blanked
        break

    if not target_sentence or not matched_surface:
        return None

    target_pos = next((v["pos"] for v in vocab_pool if v["lemma"] == lemma), None)
    distractors: list[str] = []
    seen = {matched_surface, lemma}
    used_gloss_sets: list[tuple[set[str], str]] = [
        (meta["all_glosses"], meta["en_gloss"] or "")
    ]

    ordered_pool = sorted(
        vocab_pool,
        key=lambda v: (0 if target_pos and v["pos"] == target_pos else 1, v["lemma"]),
    )
    for v in ordered_pool:
        cand = (v.get("lemma") or "").strip()
        if not cand or cand in seen or len(cand) < 2 or cand in target_sentence:
            continue
        d_meta = _get_word_meta(cand, v.get("reading") or "")
        if d_meta["en_gloss"] and any(
            _glosses_overlap(d_meta["all_glosses"], ps, d_meta["en_gloss"], pg)
            for ps, pg in used_gloss_sets
        ):
            continue
        seen.add(cand)
        if d_meta["en_gloss"]:
            used_gloss_sets.append((d_meta["all_glosses"], d_meta["en_gloss"]))
        distractors.append(cand)
        if len(distractors) >= 3:
            break

    for pad in _FALLBACK_DISTRACTORS:
        if len(distractors) >= 3:
            break
        if pad not in seen and pad not in target_sentence:
            seen.add(pad)
            distractors.append(pad)

    if len(distractors) < 3:
        return None

    choices = [matched_surface, *distractors[:3]]
    rng = random.Random(target_sentence + matched_surface)
    rng.shuffle(choices)

    hint = meta["en_gloss"] or (f"読み: {reading}" if reading else "")
    return {
        "question_id": f"q_{q_idx}",
        "kind": "cloze",
        "trouble_item_id": trouble_item_id,
        "prompt_label": "Text Context Cloze",
        "target_display": lemma,
        "blanked_sentence": blanked,
        "choices": choices,
        "correct_answer": matched_surface,
        "full_sentence": target_sentence,
        "hint": hint,
        "explanation": meta["explanation"],
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
    """Create a Bunsetsu Chunk Scramble verifying exact lossless sentence reconstruction."""
    clean = (sentence or "").strip()
    if len(clean) < 8 or len(clean) > 55:
        return None

    chunks = chunk_into_bunsetsu(clean)
    if len(chunks) < 3 or len(chunks) > 6:
        return None
    if "".join(chunks) != clean:
        return None

    shuffled = list(chunks)
    rng = random.Random(clean + f"#{q_idx}")
    rng.shuffle(shuffled)
    if shuffled == chunks:
        shuffled = shuffled[1:] + shuffled[:1]

    hint = f"Focus: {target_display}" if target_display else "Arrange the chunks into the exact sentence from the text."
    return {
        "question_id": f"q_{q_idx}",
        "kind": "scramble",
        "trouble_item_id": trouble_item_id,
        "prompt_label": "Bunsetsu Scramble",
        "target_display": target_display or chunks[0],
        "chunks": shuffled,
        "correct_answer": chunks,
        "full_sentence": clean,
        "hint": hint,
        "explanation": clean,
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
    """Create a 4-choice Particle Fix question verified against the exact sentence text."""
    parsed = extract_particle_quiz(sentence)
    if parsed is None:
        return None
    if parsed["blanked_sentence"].replace("＿＿", parsed["correct"], 1) != parsed["full_sentence"]:
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
        "hint": (
            f"Focus: {target_display}"
            if target_display
            else "Pick the exact particle used in this sentence from the text."
        ),
        "explanation": parsed["full_sentence"],
        "fallback": fallback,
    }


def _build_typed_reading_question(
    *,
    q_idx: int,
    lemma: str,
    reading: str,
    vocab_pool: list[dict[str, Any]],
    trouble_item_id: int | None,
    fallback: bool = False,
    is_aged: bool = False,
) -> dict[str, Any] | None:
    """Create an active typed Kanji -> Reading recall question validated against JMdict."""
    verified = _verified_reading_meta(lemma, reading)
    if verified is None:
        return None
    norm_reading, meta = verified

    distractors = _generate_reading_distractors(
        norm_reading,
        vocab_pool,
        forbidden_readings=set(meta["all_valid_readings"]),
    )
    choices = [norm_reading, *distractors[:3]] if len(distractors) >= 3 else []
    if choices:
        rng = random.Random(f"typed:{lemma}:{norm_reading}")
        rng.shuffle(choices)

    hint = meta["en_gloss"] or ""
    accepted = sorted(set(meta["all_valid_readings"]) | {norm_reading})
    return {
        "question_id": f"q_{q_idx}",
        "kind": "typed_reading",
        "trouble_item_id": trouble_item_id,
        "prompt_label": "Type Reading (Active Recall)",
        "target_display": lemma,
        "prompt_word": lemma,
        "prompt_text": "Type the reading in romaji or hiragana:",
        "choices": choices,
        "correct_answer": norm_reading,
        "accepted_answers": accepted,
        "full_sentence": f"{lemma}（{norm_reading}）",
        "hint": hint,
        "explanation": meta["explanation"],
        "fallback": fallback,
        "is_aged": is_aged,
    }


def _normalize_iso_utc(ts: str | None) -> str | None:
    if not ts or not ts.strip():
        return None
    try:
        dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _dispatch_vocab_question(
    *,
    mode: str,
    q_idx: int,
    lemma: str,
    reading: str,
    surface_candidates: list[str],
    preferred_sentence: str,
    sentences: list[dict[str, str]],
    vocab_pool: list[dict[str, Any]],
    trouble_item_id: int | None,
    fallback: bool,
    is_aged: bool,
    avoid_sentences: set[str] | None = None,
    avoid_kind_sentences: set[tuple[str, str]] | None = None,
) -> dict[str, Any] | None:
    if mode == "typed_reading":
        return _build_typed_reading_question(
            q_idx=q_idx,
            lemma=lemma,
            reading=reading,
            vocab_pool=vocab_pool,
            trouble_item_id=trouble_item_id,
            fallback=fallback,
            is_aged=is_aged,
        )
    if mode == "reading_recall":
        q = _build_reading_recall_question(
            q_idx=q_idx,
            lemma=lemma,
            reading=reading,
            vocab_pool=vocab_pool,
            trouble_item_id=trouble_item_id,
            fallback=fallback,
        )
    elif mode == "meaning_match":
        q = _build_meaning_match_question(
            q_idx=q_idx,
            lemma=lemma,
            reading=reading,
            vocab_pool=vocab_pool,
            trouble_item_id=trouble_item_id,
            sent_text=preferred_sentence,
            fallback=fallback,
        )
    elif mode == "def_to_word":
        q = _build_def_to_word_question(
            q_idx=q_idx,
            lemma=lemma,
            reading=reading,
            vocab_pool=vocab_pool,
            trouble_item_id=trouble_item_id,
            fallback=fallback,
        )
    elif mode == "cloze":
        q = _build_cloze_question(
            q_idx=q_idx,
            lemma=lemma,
            reading=reading,
            surface_candidates=surface_candidates,
            preferred_sentence=preferred_sentence,
            sentences=sentences,
            vocab_pool=vocab_pool,
            trouble_item_id=trouble_item_id,
            fallback=fallback,
            avoid_sentences=avoid_sentences,
            avoid_kind_sentences=avoid_kind_sentences,
        )
    else:
        return None
    if q is not None:
        q["is_aged"] = is_aged
    return q


def generate_quiz(
    *,
    book_id: int,
    chapter_index: int,
    trigger_type: str = "chapter",
    max_questions: int = 10,
    session_started_at: str | None = None,
) -> dict[str, Any]:
    """Build an adaptive mini-game mixing aged spaced recall and fresh active-recall items."""
    sentences = _load_chapter_sentences(book_id, chapter_index)
    vocab_pool = extract_chapter_vocab_pool(sentences)
    norm_session_ts = _normalize_iso_utc(session_started_at)
    now_dt = datetime.now(timezone.utc)

    with db.get_conn() as conn:
        book_row = conn.execute("SELECT title FROM books WHERE id = ?", (book_id,)).fetchone()
        ch_row = conn.execute(
            "SELECT title FROM chapters WHERE book_id = ? AND chapter_index = ?",
            (book_id, chapter_index),
        ).fetchone()
        if norm_session_ts:
            trouble_rows = conn.execute(
                """
                SELECT * FROM trouble_items
                WHERE retired = 0 AND (book_id = ? OR book_id IS NULL)
                ORDER BY
                    CASE WHEN updated_at >= ? THEN 0 ELSE 1 END ASC,
                    CASE WHEN last_chapter_index = ? THEN 0 ELSE 1 END ASC,
                    trouble_score DESC,
                    updated_at DESC
                LIMIT 50
                """,
                (book_id, norm_session_ts, chapter_index),
            ).fetchall()
        else:
            trouble_rows = conn.execute(
                """
                SELECT * FROM trouble_items
                WHERE retired = 0 AND (book_id = ? OR book_id IS NULL)
                ORDER BY
                    CASE WHEN last_chapter_index = ? THEN 0 ELSE 1 END ASC,
                    trouble_score DESC,
                    updated_at DESC
                LIMIT 50
                """,
                (book_id, chapter_index),
            ).fetchall()

    all_active = [dict(r) for r in trouble_rows]
    ready_aged: list[dict[str, Any]] = []
    fresh_active: list[dict[str, Any]] = []

    for row in all_active:
        stage = int(row.get("memory_stage") or 0)
        due_dt = db._parse_iso_utc(row.get("next_review_after"))
        if stage >= 1:
            if due_dt is None or due_dt <= now_dt:
                ready_aged.append(row)
        else:
            fresh_active.append(row)

    ready_aged.sort(
        key=lambda r: (
            r.get("next_review_after") or "",
            -float(r.get("trouble_score") or 0.0),
        )
    )

    slot_limit = max_questions

    # Prioritize aged items (up to half the round, minimum 3 when available) mixed with fresh Stage 0 items
    aged_cap = min(max(3, max_questions // 2), slot_limit)
    ordered_queue: list[tuple[dict[str, Any], bool]] = [
        (item, True) for item in ready_aged[:aged_cap]
    ] + [(item, False) for item in fresh_active]
    if len(ready_aged) > aged_cap:
        ordered_queue.extend((item, True) for item in ready_aged[aged_cap:])

    questions: list[dict[str, Any]] = []
    used_sentences: set[str] = set()
    used_kind_sentences: set[tuple[str, str]] = set()
    used_trouble_ids: set[int] = set()
    used_words: set[str] = set()
    cloze_count = 0
    match_pairs_used = False
    grammar_toggle = 0
    vocab_toggle = 0

    _FRESH_ROTATIONS = (
        ("typed_reading", "meaning_match", "def_to_word", "reading_recall", "cloze"),
        ("meaning_match", "typed_reading", "def_to_word", "reading_recall", "cloze"),
        ("def_to_word", "reading_recall", "meaning_match", "typed_reading", "cloze"),
        ("reading_recall", "def_to_word", "meaning_match", "typed_reading", "cloze"),
    )
    _AGED_ROTATION = ("typed_reading", "def_to_word", "meaning_match", "reading_recall", "cloze")

    for idx, (item, is_aged) in enumerate(ordered_queue):
        if len(questions) >= slot_limit:
            break
        item_id = int(item["id"])
        if item_id in used_trouble_ids:
            continue
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
                    candidate["is_aged"] = is_aged
                    q = candidate
                    break
            grammar_toggle += 1
        else:
            remaining_trouble_words = [
                {
                    "lemma": t["display_text"],
                    "reading": t.get("reading") or "",
                    "trouble_item_id": int(t["id"]),
                }
                for t, _ in ordered_queue[idx:]
                if t["item_type"] == "word" and int(t["id"]) not in used_trouble_ids
            ]
            if (
                not match_pairs_used
                and len(questions) == slot_limit - 1
                and len(remaining_trouble_words) >= 2
            ):
                pool_extras = [
                    {
                        "lemma": v["lemma"],
                        "reading": v["reading"],
                        "trouble_item_id": None,
                    }
                    for v in vocab_pool
                    if v["lemma"] not in used_words
                ]
                q = _build_match_pairs_question(
                    q_idx=len(questions),
                    candidate_words=remaining_trouble_words + pool_extras,
                )
                if q is not None:
                    q["is_aged"] = is_aged
                    match_pairs_used = True
                    for tid in q.get("trouble_item_ids") or []:
                        used_trouble_ids.add(tid)
                    for p in q.get("pairs") or []:
                        used_words.add(p["word"])

            if q is None:
                lemma = item["display_text"]
                reading = item.get("reading") or ""
                surf = _lookup_surface_for_lemma(book_id, chapter_index, lemma, item.get("sent_id"))
                candidates = [c for c in (surf, lemma) if c]
                pref_sent = (item.get("sent_text") or "").strip()

                rotation = _AGED_ROTATION if is_aged else _FRESH_ROTATIONS[vocab_toggle % len(_FRESH_ROTATIONS)]
                for mode in rotation:
                    if mode == "cloze" and cloze_count >= 1:
                        continue
                    q = _dispatch_vocab_question(
                        mode=mode,
                        q_idx=len(questions),
                        lemma=lemma,
                        reading=reading,
                        surface_candidates=candidates,
                        preferred_sentence=pref_sent,
                        sentences=sentences,
                        vocab_pool=vocab_pool,
                        trouble_item_id=item_id,
                        fallback=False,
                        is_aged=is_aged,
                        avoid_sentences=used_sentences,
                        avoid_kind_sentences=used_kind_sentences,
                    )
                    if q is not None:
                        break
                vocab_toggle += 1
                if q is not None:
                    used_words.add(lemma)

        if q is not None:
            questions.append(q)
            if q["kind"] == "cloze":
                cloze_count += 1
            if q["kind"] == "match_pairs":
                match_pairs_used = True
            else:
                used_trouble_ids.add(item_id)
            used_sentences.add(q["full_sentence"])
            used_kind_sentences.add((q["kind"], q["full_sentence"]))

    # Top up remaining main slots with chapter fallback material
    if len(questions) < slot_limit and (sentences or vocab_pool):
        have_kinds = {q["kind"] for q in questions}

        if not match_pairs_used and len(questions) < slot_limit and vocab_pool:
            pool_cands = [
                {"lemma": v["lemma"], "reading": v["reading"], "trouble_item_id": None}
                for v in vocab_pool
                if v["lemma"] not in used_words
            ]
            mq = _build_match_pairs_question(
                q_idx=len(questions),
                candidate_words=pool_cands,
                fallback=True,
            )
            if mq is not None:
                mq["is_aged"] = False
                questions.append(mq)
                match_pairs_used = True
                have_kinds.add("match_pairs")
                for p in mq.get("pairs") or []:
                    used_words.add(p["word"])

        for sent_obj in sentences:
            if len(questions) >= slot_limit:
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
                q["is_aged"] = False
                questions.append(q)
                used_sentences.add(q["full_sentence"])
                have_kinds.add(q["kind"])

        for v in vocab_pool:
            if len(questions) >= slot_limit:
                break
            lemma = v["lemma"]
            if lemma in used_words:
                continue
            reading = v["reading"]
            q = None
            rotation = _FRESH_ROTATIONS[vocab_toggle % len(_FRESH_ROTATIONS)]
            for mode in rotation:
                if mode == "cloze" and cloze_count >= 1:
                    continue
                q = _dispatch_vocab_question(
                    mode=mode,
                    q_idx=len(questions),
                    lemma=lemma,
                    reading=reading,
                    surface_candidates=[v["surface"], lemma],
                    preferred_sentence=v["sentence_text"],
                    sentences=sentences,
                    vocab_pool=vocab_pool,
                    trouble_item_id=None,
                    fallback=True,
                    is_aged=False,
                )
                if q is not None:
                    break
            vocab_toggle += 1
            if q is not None:
                questions.append(q)
                used_words.add(lemma)
                if q["kind"] == "cloze":
                    cloze_count += 1
                used_sentences.add(q["full_sentence"])

        for sent_obj in sentences:
            if len(questions) >= slot_limit:
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
                q["is_aged"] = False
                questions.append(q)
                used_sentences.add(q["full_sentence"])

    for idx, q in enumerate(questions):
        q["question_id"] = f"q_{idx}"

    return {
        "book_id": book_id,
        "book_title": book_row["title"] if book_row else "",
        "chapter_index": chapter_index,
        "chapter_title": ch_row["title"] if ch_row else "",
        "trigger_type": trigger_type,
        "trouble_based_count": len(used_trouble_ids),
        "aged_count": sum(1 for q in questions if q.get("is_aged")),
        "questions": questions,
    }


def submit_quiz(
    *,
    book_id: int | None,
    chapter_index: int | None,
    trigger_type: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Score a completed quiz round, advance multi-stage trouble memory, and save history."""
    conquered: list[dict[str, Any]] = []
    still_tricky: list[dict[str, Any]] = []
    scored_results = [item for item in results if not item.get("skipped")]
    correct_count = 0
    seen_updated_ids: set[int] = set()

    for item in scored_results:
        is_correct = bool(item.get("correct"))
        if is_correct:
            correct_count += 1

        target_ids: list[int] = []
        if item.get("trouble_item_id") is not None:
            target_ids.append(int(item["trouble_item_id"]))
        for extra_id in item.get("trouble_item_ids") or []:
            if extra_id is not None and int(extra_id) not in target_ids:
                target_ids.append(int(extra_id))

        for tid in target_ids:
            if tid in seen_updated_ids:
                continue
            seen_updated_ids.add(tid)
            updated = db.apply_quiz_item_result(tid, is_correct)
            if updated is not None:
                summary = {
                    "id": updated["id"],
                    "display_text": updated["display_text"],
                    "item_type": updated["item_type"],
                    "trouble_score": updated["trouble_score"],
                    "memory_stage": updated["memory_stage"],
                    "next_review_after": updated["next_review_after"],
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
                len(scored_results),
                correct_count,
                json.dumps(results, ensure_ascii=False),
                now,
            ),
        )
        session_id = int(cur.lastrowid)

    return {
        "session_id": session_id,
        "score": correct_count,
        "total": len(scored_results),
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
