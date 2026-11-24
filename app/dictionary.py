"""Thread-safe offline Japanese-English dictionary lookups via Jamdict (JMdict)."""

from __future__ import annotations

import re
import threading
from typing import Any

from jamdict import Jamdict

from app import config

_THREAD_LOCAL = threading.local()
_KANJI_RE = re.compile(r"[\u4e00-\u9fff]")


def _to_hiragana(text: str) -> str:
    """Convert full-width katakana in `text` to hiragana for reading comparisons."""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        elif ch == "ヴ":
            out.append("う゛")
        else:
            out.append(ch)
    return "".join(out)


def _serialize_entry(entry: Any) -> dict[str, Any] | None:
    senses = []
    for sense in entry.senses[:6]:
        glosses = [g.text for g in sense.gloss if getattr(g, "text", None)]
        if not glosses:
            continue
        senses.append(
            {
                "pos": [str(p) for p in sense.pos],
                "glosses": glosses,
            }
        )
    if not senses:
        return None
    return {
        "idseq": entry.idseq,
        "kanji": [k.text for k in entry.kanji_forms],
        "kana": [k.text for k in entry.kana_forms],
        "senses": senses,
    }


def _get_jamdict() -> Jamdict | None:
    """Return a per-thread Jamdict instance backed by data/jamdict.db."""
    if not config.JAMDICT_DB_PATH.is_file():
        return None
    instance = getattr(_THREAD_LOCAL, "jamdict", None)
    db_path_str = str(config.JAMDICT_DB_PATH.resolve())
    if instance is None or getattr(_THREAD_LOCAL, "db_path", None) != db_path_str:
        instance = Jamdict(db_file=db_path_str, auto_config=False)
        _THREAD_LOCAL.jamdict = instance
        _THREAD_LOCAL.db_path = db_path_str
    return instance


def _score_entry(entry: Any, targets: set[str]) -> tuple[int, int]:
    """Rank dictionary entries by exact kanji/kana match and shorter gloss length."""
    kanji_texts = [k.text for k in entry.kanji_forms]
    kana_texts = [k.text for k in entry.kana_forms]
    exact_kanji = any(k in targets for k in kanji_texts)
    exact_kana = any(k in targets for k in kana_texts)
    score = 0
    if exact_kanji:
        score += 4
    if exact_kana:
        score += 2
    return (-score, len(kanji_texts) + len(kana_texts))


def lookup_english(
    lemma: str,
    norm_form: str = "",
    surface: str = "",
    reading: str = "",
    limit: int = 6,
) -> dict[str, Any]:
    """Look up English JMdict definitions offline, trying base and fallback forms."""
    jmd = _get_jamdict()
    if jmd is None:
        return {
            "query": lemma or surface,
            "entries": [],
            "missing_db": True,
            "error": (
                "Offline JMdict database not found at data/jamdict.db. "
                "Run `uv run python scripts/setup_dict.py` to download it."
            ),
        }

    has_kanji = bool(_KANJI_RE.search(lemma or surface or ""))
    candidates: list[str] = []
    raw_cands = (lemma, norm_form, surface) if has_kanji else (lemma, norm_form, surface, reading)
    for cand in raw_cands:
        c = (cand or "").strip()
        if c and c not in candidates:
            candidates.append(c)

    if not candidates:
        return {"query": "", "entries": []}

    targets = set(candidates)
    matched_entries: list[Any] = []
    used_query = candidates[0]

    for cand in candidates:
        res = jmd.lookup(cand)
        if res.entries:
            matched_entries = list(res.entries)
            used_query = cand
            break

    # Deduplicate by idseq and sort by exact-form relevance
    seen_ids: set[int] = set()
    unique_entries: list[Any] = []
    for entry in matched_entries:
        if entry.idseq not in seen_ids:
            seen_ids.add(entry.idseq)
            unique_entries.append(entry)

    unique_entries.sort(key=lambda e: _score_entry(e, targets))

    serialized: list[dict[str, Any]] = []
    for entry in unique_entries[:limit]:
        item = _serialize_entry(entry)
        if item is not None:
            serialized.append(item)

    return {
        "query": used_query,
        "entries": serialized,
    }


def lookup_english_exact(
    lemma: str,
    reading: str = "",
    limit: int = 6,
) -> dict[str, Any]:
    """Look up JMdict entries requiring an exact headword (and reading when provided)."""
    clean_lemma = (lemma or "").strip()
    clean_reading_raw = (reading or "").strip()
    clean_reading_hira = (
        "" if _KANJI_RE.search(clean_reading_raw) else _to_hiragana(clean_reading_raw)
    )

    jmd = _get_jamdict()
    if jmd is None:
        return {
            "query": clean_lemma,
            "entries": [],
            "all_valid_readings": [],
            "all_glosses": [],
            "missing_db": True,
        }

    if not clean_lemma:
        return {
            "query": "",
            "entries": [],
            "all_valid_readings": [],
            "all_glosses": [],
        }

    res = jmd.lookup(clean_lemma)
    seen_ids: set[int] = set()
    unique_entries: list[Any] = []
    for entry in res.entries:
        if entry.idseq not in seen_ids:
            seen_ids.add(entry.idseq)
            unique_entries.append(entry)

    has_kanji = bool(_KANJI_RE.search(clean_lemma))
    all_valid_readings: list[str] = []
    seen_readings: set[str] = set()

    if has_kanji:
        kanji_matched = [
            e for e in unique_entries if clean_lemma in [k.text for k in e.kanji_forms]
        ]
        for entry in kanji_matched:
            for kf in entry.kana_forms:
                h = _to_hiragana(kf.text.strip())
                if h and h not in seen_readings:
                    seen_readings.add(h)
                    all_valid_readings.append(h)

        if clean_reading_hira:
            matched_entries = [
                e
                for e in kanji_matched
                if clean_reading_hira in [_to_hiragana(k.text.strip()) for k in e.kana_forms]
            ]
        else:
            matched_entries = kanji_matched
    else:
        lemma_hira = _to_hiragana(clean_lemma)
        matched_entries = [
            e
            for e in unique_entries
            if clean_lemma in [k.text for k in e.kana_forms]
            or lemma_hira in [_to_hiragana(k.text.strip()) for k in e.kana_forms]
        ]
        if matched_entries and lemma_hira:
            all_valid_readings = [lemma_hira]

    matched_entries.sort(key=lambda e: _score_entry(e, {clean_lemma}))

    all_glosses: set[str] = set()
    for entry in matched_entries:
        for sense in entry.senses:
            for g in sense.gloss:
                txt = (getattr(g, "text", None) or "").strip().lower()
                if txt:
                    all_glosses.add(txt)

    serialized: list[dict[str, Any]] = []
    for entry in matched_entries[:limit]:
        item = _serialize_entry(entry)
        if item is not None:
            serialized.append(item)

    return {
        "query": clean_lemma,
        "entries": serialized,
        "all_valid_readings": all_valid_readings,
        "all_glosses": sorted(all_glosses),
    }
