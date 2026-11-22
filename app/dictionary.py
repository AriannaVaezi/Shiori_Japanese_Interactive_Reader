"""Thread-safe offline Japanese-English dictionary lookups via Jamdict (JMdict)."""

from __future__ import annotations

import threading
from typing import Any

from jamdict import Jamdict

from app import config

_THREAD_LOCAL = threading.local()


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

    candidates: list[str] = []
    for cand in (lemma, norm_form, surface, reading):
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
            continue
        serialized.append(
            {
                "idseq": entry.idseq,
                "kanji": [k.text for k in entry.kanji_forms],
                "kana": [k.text for k in entry.kana_forms],
                "senses": senses,
            }
        )

    return {
        "query": used_query,
        "entries": serialized,
    }
