"""Zero-backlog adaptive mini-game generator and session scoring service."""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone
from typing import Any

from app import db, dictionary, llm
from app.services import books_service
from app.tokenizer import (
    chunk_into_bunsetsu,
    extract_chapter_vocab_pool,
    extract_particle_quiz,
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


def _get_word_meta(lemma: str, reading: str) -> dict[str, Any]:
    """Fetch cached J-J definition/examples and concise offline JMdict gloss."""
    jj_def: str | None = None
    jj_examples: list[str] = []
    en_gloss: str | None = None
    explanation_parts: list[str] = []

    with db.get_conn() as conn:
        if reading:
            row = conn.execute(
                "SELECT jj_json, en_json FROM dict_cache WHERE lemma = ? AND reading = ?",
                (lemma, reading),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT jj_json, en_json FROM dict_cache WHERE lemma = ? ORDER BY updated_at DESC LIMIT 1",
                (lemma,),
            ).fetchone()

    if row and row["jj_json"]:
        jj = json.loads(row["jj_json"])
        if jj.get("simple_definition"):
            jj_def = str(jj["simple_definition"]).strip()
            explanation_parts.append(jj_def)
        for ex in jj.get("examples") or []:
            if isinstance(ex, dict) and ex.get("jp"):
                jj_examples.append(str(ex["jp"]).strip())

    en_data = json.loads(row["en_json"]) if (row and row["en_json"]) else None
    if en_data is None:
        en_data = dictionary.lookup_english(lemma=lemma, surface=lemma, reading=reading)
    en_gloss = _short_en_gloss(en_data)
    if en_gloss:
        explanation_parts.append(f"EN: {en_gloss}")

    head = f"{lemma}（{reading}）" if reading and reading != lemma else lemma
    explanation = f"{head} — {' / '.join(explanation_parts)}" if explanation_parts else head
    return {
        "jj_def": jj_def,
        "jj_examples": jj_examples,
        "en_gloss": en_gloss,
        "explanation": explanation,
    }


def _load_cached_jj_pool(exclude_lemma: str, limit: int = 30) -> list[dict[str, str]]:
    """Return other cached words that have easy-Japanese definitions."""
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT lemma, reading, jj_json FROM dict_cache
            WHERE jj_json IS NOT NULL AND lemma != ?
            ORDER BY updated_at DESC LIMIT ?
            """,
            (exclude_lemma, limit),
        ).fetchall()

    out: list[dict[str, str]] = []
    for r in rows:
        try:
            jj = json.loads(r["jj_json"])
        except (TypeError, ValueError):
            continue
        defn = str(jj.get("simple_definition") or "").strip()
        if defn:
            out.append({"lemma": r["lemma"], "reading": r["reading"], "jj_def": defn})
    return out


def _generate_reading_distractors(reading: str, vocab_pool: list[dict[str, Any]]) -> list[str]:
    """Generate 3 plausible hiragana distractors for a kanji word's reading."""
    distractors: list[str] = []
    seen = {reading}

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
    """Create a Kanji -> Hiragana reading recall question without sentence context."""
    if not lemma or not reading or len(reading) < 2 or reading == lemma:
        return None
    if not _KANJI_RE.search(lemma):
        return None

    distractors = _generate_reading_distractors(reading, vocab_pool)
    if len(distractors) < 3:
        return None

    choices = [reading, *distractors[:3]]
    rng = random.Random(f"read:{lemma}:{reading}")
    rng.shuffle(choices)

    meta = _get_word_meta(lemma, reading)
    hint = meta["jj_def"] or (f"EN: {meta['en_gloss']}" if meta["en_gloss"] else "")
    return {
        "question_id": f"q_{q_idx}",
        "kind": "reading_recall",
        "trouble_item_id": trouble_item_id,
        "prompt_label": "Kanji → Reading",
        "target_display": lemma,
        "prompt_word": lemma,
        "prompt_text": "How do you read this word?",
        "choices": choices,
        "correct_answer": reading,
        "full_sentence": f"{lemma}（{reading}）",
        "hint": hint,
        "explanation": meta["explanation"],
        "fallback": fallback,
    }


def _collect_en_distractor_words(
    *,
    exclude_lemma: str,
    exclude_gloss: str,
    vocab_pool: list[dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, str]]:
    """Collect distinct words + concise JMdict English glosses for distractors."""
    out: list[dict[str, str]] = []
    seen_lemmas = {exclude_lemma}
    seen_glosses = {exclude_gloss.lower()}

    for v in vocab_pool[:25]:
        w = v["lemma"]
        r = v.get("reading") or ""
        if w in seen_lemmas:
            continue
        meta = _get_word_meta(w, r)
        g = meta["en_gloss"]
        if g and g.lower() not in seen_glosses:
            seen_lemmas.add(w)
            seen_glosses.add(g.lower())
            out.append({"lemma": w, "reading": r, "gloss": g})
        if len(out) >= limit:
            return out

    for w, r, g in _FALLBACK_VOCAB_ENTRIES:
        if len(out) >= limit:
            break
        if w not in seen_lemmas and g.lower() not in seen_glosses:
            seen_lemmas.add(w)
            seen_glosses.add(g.lower())
            out.append({"lemma": w, "reading": r, "gloss": g})

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
    """Create a standalone Word -> Meaning question (same-language choices, no plot context)."""
    meta = _get_word_meta(lemma, reading)
    correct_meaning: str | None = None
    distractor_meanings: list[str] = []
    prompt_text = "What does this word mean?"

    # Prefer pure J-J mode when >= 3 other J-J cached definitions are available
    if meta["jj_def"]:
        jj_pool = _load_cached_jj_pool(exclude_lemma=lemma)
        jj_dists: list[str] = []
        seen_defs = {meta["jj_def"]}
        for item in jj_pool:
            if item["jj_def"] not in seen_defs:
                seen_defs.add(item["jj_def"])
                jj_dists.append(item["jj_def"])
            if len(jj_dists) >= 3:
                break
        if len(jj_dists) >= 3:
            correct_meaning = meta["jj_def"]
            distractor_meanings = jj_dists
            prompt_text = "Choose the matching Japanese meaning:"

    if correct_meaning is None and meta["en_gloss"]:
        correct_meaning = meta["en_gloss"]
        dist_entries = _collect_en_distractor_words(
            exclude_lemma=lemma,
            exclude_gloss=correct_meaning,
            vocab_pool=vocab_pool,
            limit=3,
        )
        distractor_meanings = [d["gloss"] for d in dist_entries]

    if correct_meaning is None or len(distractor_meanings) < 3:
        return None

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
        "prompt_text": prompt_text,
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
    """Create a reverse Meaning -> Word recall question."""
    meta = _get_word_meta(lemma, reading)
    prompt_def = meta["jj_def"] or meta["en_gloss"]
    if not prompt_def:
        return None

    distractor_words: list[str] = []
    seen = {lemma}
    for d in _collect_en_distractor_words(
        exclude_lemma=lemma,
        exclude_gloss=meta["en_gloss"] or prompt_def,
        vocab_pool=vocab_pool,
        limit=3,
    ):
        if d["lemma"] not in seen:
            seen.add(d["lemma"])
            distractor_words.append(d["lemma"])

    for fb_w, _, _ in _FALLBACK_VOCAB_ENTRIES:
        if len(distractor_words) >= 3:
            break
        if fb_w not in seen:
            seen.add(fb_w)
            distractor_words.append(fb_w)

    if len(distractor_words) < 3:
        return None

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
    """Create a 4-pair interactive Word <-> Meaning match-up question."""
    enriched: list[dict[str, Any]] = []
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
        seen_lemmas.add(lemma)
        meta = _get_word_meta(lemma, reading)
        en_gloss = meta["en_gloss"] or item.get("_fb_gloss")
        if not meta["jj_def"] and not en_gloss:
            continue
        enriched.append(
            {
                "lemma": lemma,
                "reading": reading,
                "jj_def": meta["jj_def"],
                "en_gloss": en_gloss,
                "trouble_item_id": item.get("trouble_item_id"),
            }
        )
        if len(enriched) >= 16:
            break

    # Pick 4 with J-J if at least 4 distinct J-J definitions exist; otherwise 4 English glosses
    chosen: list[dict[str, Any]] = []
    use_jj = False
    jj_candidates = [e for e in enriched if e["jj_def"]]
    if len(jj_candidates) >= 4:
        seen_defs: set[str] = set()
        for e in jj_candidates:
            if e["jj_def"] not in seen_defs:
                seen_defs.add(e["jj_def"])
                chosen.append(e)
            if len(chosen) == 4:
                use_jj = True
                break

    if len(chosen) < 4:
        chosen = []
        seen_glosses: set[str] = set()
        for e in enriched:
            g = e["en_gloss"]
            if g and g.lower() not in seen_glosses:
                seen_glosses.add(g.lower())
                chosen.append(e)
            if len(chosen) == 4:
                break

    if len(chosen) < 4:
        return None

    pairs = []
    left_items = []
    right_items = []
    trouble_ids: list[int] = []

    for idx, entry in enumerate(chosen):
        meaning = entry["jj_def"] if use_jj else entry["en_gloss"]
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
        "prompt_text": "Match each Japanese word to its meaning:",
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
    """Create a 4-choice Context Cloze question, preferring fresh non-book sentences when cached."""
    meta = _get_word_meta(lemma, reading)
    target_sentence = ""
    matched_surface = ""
    is_fresh_sentence = False

    blocked_cloze = {st for k, st in (avoid_kind_sentences or set()) if k == "cloze"}

    # Prefer fresh everyday example sentences from J-J cache so the user can't just recall plot
    for ex in meta["jj_examples"]:
        if ex in blocked_cloze:
            continue
        for cand in [lemma, *surface_candidates]:
            if cand and cand in ex:
                target_sentence = ex
                matched_surface = cand
                is_fresh_sentence = True
                break
        if target_sentence:
            break

    if not target_sentence:
        candidate_sentences: list[str] = []
        if preferred_sentence:
            candidate_sentences.append(preferred_sentence)
        for s in sentences:
            txt = s.get("text", "")
            if txt and txt not in candidate_sentences:
                candidate_sentences.append(txt)

        if avoid_sentences:
            candidate_sentences.sort(key=lambda st: (1 if st in avoid_sentences else 0))

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

    hint = meta["jj_def"] or (f"読み: {reading}" if reading else "")
    return {
        "question_id": f"q_{q_idx}",
        "kind": "cloze",
        "trouble_item_id": trouble_item_id,
        "prompt_label": "Fresh Context Cloze" if is_fresh_sentence else "Context Cloze",
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
    """Create an active typed Kanji -> Reading recall question (with optional safety-net choices)."""
    if not lemma or not reading or len(reading) < 2 or reading == lemma:
        return None
    if not _KANJI_RE.search(lemma):
        return None

    distractors = _generate_reading_distractors(reading, vocab_pool)
    choices = [reading, *distractors[:3]] if len(distractors) >= 3 else []
    if choices:
        rng = random.Random(f"typed:{lemma}:{reading}")
        rng.shuffle(choices)

    meta = _get_word_meta(lemma, reading)
    hint = meta["jj_def"] or (f"EN: {meta['en_gloss']}" if meta["en_gloss"] else "")
    return {
        "question_id": f"q_{q_idx}",
        "kind": "typed_reading",
        "trouble_item_id": trouble_item_id,
        "prompt_label": "Type Reading (Active Recall)",
        "target_display": lemma,
        "prompt_word": lemma,
        "prompt_text": "Type the reading in romaji or hiragana:",
        "choices": choices,
        "correct_answer": reading,
        "full_sentence": f"{lemma}（{reading}）",
        "hint": hint,
        "explanation": meta["explanation"],
        "fallback": fallback,
        "is_aged": is_aged,
    }


def _build_sentence_output_question(
    *,
    q_idx: int,
    target_word: str,
    reading: str,
    item_type: str,
    trouble_item_id: int | None,
    sent_text: str = "",
    fallback: bool = False,
    is_aged: bool = False,
) -> dict[str, Any] | None:
    """Create a 1-sentence AI-coached active production challenge."""
    clean_target = (target_word or "").strip()
    if not clean_target:
        return None

    meta = _get_word_meta(clean_target, reading) if item_type == "word" else {
        "jj_def": None,
        "jj_examples": [],
        "en_gloss": None,
        "explanation": sent_text or clean_target,
    }
    meaning_hint = meta["jj_def"] or meta["en_gloss"] or ""
    if not meaning_hint and item_type == "grammar":
        meaning_hint = f"Pattern from: {sent_text}" if sent_text else "Practice this expression in a new sentence."
    example_hint = (meta["jj_examples"][0] if meta["jj_examples"] else sent_text) or ""

    return {
        "question_id": f"q_{q_idx}",
        "kind": "sentence_output",
        "trouble_item_id": trouble_item_id,
        "item_type": item_type,
        "prompt_label": "Use It in a Sentence",
        "target_display": clean_target,
        "prompt_word": clean_target,
        "reading": reading,
        "prompt_text": f"Write 1 short Japanese sentence using 「{clean_target}」 (simple Japanese is fine!):",
        "meaning_hint": meaning_hint,
        "example_hint": example_hint,
        "correct_answer": clean_target,
        "full_sentence": example_hint or clean_target,
        "hint": meaning_hint,
        "explanation": meta["explanation"],
        "fallback": fallback,
        "is_aged": is_aged,
    }


_OUTPUT_EVAL_SYSTEM_PROMPT = """あなたは親切な日本語学習コーチです。
学習者が指定された単語または文法・表現を使って短い日本語の文を1つ書きました。
完璧でなくても、意味が通じていてターゲットの語句を使おうとしていれば合格（passed: true）にしてください。

必ず次のJSON形式のみを返してください（マークダウン不要）:
{
  "passed": true,
  "verdict": "1-sentence encouraging evaluation in English",
  "grammar_tip": "1-sentence particle, conjugation, or nuance tip in English (or praise if already natural)",
  "natural_version": "1 natural Japanese sentence expressing the learner's idea"
}"""


_OUTPUT_EVAL_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "passed": {"type": "boolean"},
        "verdict": {"type": "string", "minLength": 1},
        "grammar_tip": {"type": "string"},
        "natural_version": {"type": "string"},
    },
    "required": ["passed", "verdict", "grammar_tip", "natural_version"],
    "additionalProperties": False,
}


async def evaluate_output_sentence(
    *,
    target_word: str,
    reading: str = "",
    item_type: str = "word",
    user_sentence: str,
    meaning_hint: str = "",
) -> dict[str, Any]:
    """Ask the configured LLM to gently coach a learner-written Japanese sentence."""
    clean_sentence = (user_sentence or "").strip()
    if not clean_sentence:
        return {
            "llm_unavailable": False,
            "passed": False,
            "verdict": "Please write a short sentence first (or click Skip).",
            "grammar_tip": "",
            "natural_version": "",
        }

    user_prompt = (
        f"ターゲット語句 ({item_type}): {target_word}"
        + (f"（{reading}）" if reading else "")
        + "\n"
        + (f"意味ヒント: {meaning_hint}\n" if meaning_hint else "")
        + f"学習者の文: {clean_sentence}\n\n"
        "この文を添削・フィードバックしてください。"
    )

    settings = db.get_all_settings()
    try:
        raw_text, provider, model = await llm.generate_text(
            settings=settings,
            system_prompt=_OUTPUT_EVAL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            expect_json=True,
            json_schema=_OUTPUT_EVAL_JSON_SCHEMA,
        )
        parsed = llm.extract_json_object(raw_text)
        verdict = str(parsed.get("verdict") or "").strip()
        if not verdict:
            raise ValueError("Missing verdict in LLM evaluation JSON.")
        return {
            "llm_unavailable": False,
            "passed": bool(parsed.get("passed", True)),
            "verdict": verdict,
            "grammar_tip": str(parsed.get("grammar_tip") or "").strip(),
            "natural_version": str(parsed.get("natural_version") or "").strip(),
            "provider": provider,
            "model": model,
        }
    except (llm.LLMError, ValueError) as exc:
        return {
            "llm_unavailable": True,
            "error": str(exc),
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
    max_questions: int = 5,
    session_started_at: str | None = None,
) -> dict[str, Any]:
    """Build an adaptive mini-game mixing aged spaced recall, fresh items, and 1 output prompt."""
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

    # Reserve 1 slot at the end for an active sentence production challenge when possible
    reserve_output = max_questions >= 4 and bool(all_active or vocab_pool)
    slot_limit = max_questions - 1 if reserve_output else max_questions

    # Prioritize aged items (up to 3) mixed with fresh Stage 0 items
    aged_cap = min(3, slot_limit)
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

    # Append 1 active sentence production prompt when reserved
    if reserve_output and len(questions) < max_questions:
        out_q: dict[str, Any] | None = None
        # Prefer an unquizzed trouble item (aged first), then chapter vocab, then any trouble item
        unquizzed = [(t, aged) for t, aged in ordered_queue if int(t["id"]) not in used_trouble_ids]
        if unquizzed:
            t_item, t_aged = unquizzed[0]
            out_q = _build_sentence_output_question(
                q_idx=len(questions),
                target_word=t_item["display_text"],
                reading=t_item.get("reading") or "",
                item_type=t_item["item_type"],
                trouble_item_id=int(t_item["id"]),
                sent_text=t_item.get("sent_text") or "",
                fallback=False,
                is_aged=t_aged,
            )
        if out_q is None:
            for v in vocab_pool:
                if v["lemma"] not in used_words:
                    out_q = _build_sentence_output_question(
                        q_idx=len(questions),
                        target_word=v["lemma"],
                        reading=v.get("reading") or "",
                        item_type="word",
                        trouble_item_id=None,
                        sent_text=v.get("sentence_text") or "",
                        fallback=True,
                        is_aged=False,
                    )
                    if out_q is not None:
                        break
        if out_q is None and ordered_queue:
            t_item, t_aged = ordered_queue[-1]
            out_q = _build_sentence_output_question(
                q_idx=len(questions),
                target_word=t_item["display_text"],
                reading=t_item.get("reading") or "",
                item_type=t_item["item_type"],
                trouble_item_id=None,
                sent_text=t_item.get("sent_text") or "",
                fallback=False,
                is_aged=t_aged,
            )
        if out_q is not None:
            questions.append(out_q)

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
