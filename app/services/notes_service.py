"""Saved book passages, quotes, highlights, and free-form study notes service."""

from __future__ import annotations

import difflib
import re
from typing import Any

from app import db

ALLOWED_CATEGORIES = {"quote", "note", "expression"}
ALLOWED_COLORS = {"", "yellow", "blue", "pink", "orange", "green", "purple"}
_INLINE_HL_RE = re.compile(r"==(?:\[(?:yellow|blue|pink|orange|green|purple)\])?([\s\S]+?)==")
_PUNCT_EDGE_RE = re.compile(
    r"^[\s。、．，.,!?！？「」『』（）()【】［］\[\]・…—―\-〜~\"'“”‘’]+|"
    r"[\s。、．，.,!?！？「」『』（）()【】［］\[\]・…—―\-〜~\"'“”‘’]+$"
)


def _compact_quote(text: str | None) -> str:
    plain = _INLINE_HL_RE.sub(r"\1", text or "")
    return re.sub(r"\s+", "", plain)


def _core_quote(text: str | None) -> str:
    compact = _compact_quote(text)
    stripped = _PUNCT_EDGE_RE.sub("", compact)
    return stripped or compact


def _find_matching_notes(
    existing_rows: list[Any],
    *,
    clean_quote: str,
    clean_sent_id: str | None,
    target_note_ids: list[int] | None = None,
) -> list[Any]:
    id_set = {int(i) for i in (target_note_ids or []) if i is not None}
    target_compact = _compact_quote(clean_quote)
    target_core = _core_quote(clean_quote)
    matches: list[Any] = []
    for r in existing_rows:
        if r["id"] in id_set:
            matches.append(r)
            continue
        if target_compact:
            r_compact = _compact_quote(r["quote_text"])
            r_core = _core_quote(r["quote_text"])
            if not r_compact:
                continue
            same_or_open_sent = (
                clean_sent_id is None
                or r["sent_id"] is None
                or r["sent_id"] == clean_sent_id
            )
            exact_match = (
                r_compact == target_compact
                or (r_core == target_core and len(target_core) >= 2)
            ) and (same_or_open_sent or len(target_core) >= 4)
            contained_match = (
                bool(target_core and r_core)
                and (target_core in r_core or r_core in target_core)
                and min(len(target_core), len(r_core)) >= 2
                and (same_or_open_sent or min(len(target_core), len(r_core)) >= 6)
            )
            similar_match = (
                same_or_open_sent
                and min(len(target_core), len(r_core)) >= 4
                and difflib.SequenceMatcher(None, r_core, target_core).ratio() >= 0.75
            )
            if exact_match or contained_match or similar_match:
                matches.append(r)
        elif clean_sent_id and r["sent_id"] == clean_sent_id:
            matches.append(r)
    return matches

_SELECT_JOIN = """
SELECT n.*,
       b.title AS book_title,
       b.media_type AS media_type,
       c.title AS chapter_title
FROM reader_notes n
JOIN books b ON b.id = n.book_id
LEFT JOIN chapters c ON c.book_id = n.book_id AND c.chapter_index = n.chapter_index
"""


def _clean_category(category: str | None) -> str:
    val = (category or "quote").strip().lower()
    return val if val in ALLOWED_CATEGORIES else "quote"


def _clean_color(color: str | None, default: str = "yellow") -> str:
    if color is None:
        return default
    val = color.strip().lower()
    return val if val in ALLOWED_COLORS else default


def list_notes(
    *,
    book_id: int | None = None,
    chapter_index: int | None = None,
    category: str | None = None,
    color: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return saved highlights and notes filtered optionally by book, chapter, category, or color."""
    clauses: list[str] = []
    params: list[Any] = []
    if book_id is not None:
        clauses.append("n.book_id = ?")
        params.append(book_id)
    if chapter_index is not None:
        clauses.append("n.chapter_index = ?")
        params.append(chapter_index)
    if category and category.strip().lower() in ALLOWED_CATEGORIES:
        clauses.append("n.category = ?")
        params.append(category.strip().lower())
    clean_color = (color or "").strip().lower()
    if clean_color and clean_color in ALLOWED_COLORS:
        pattern = f"%==[{clean_color}]%"
        clauses.append("(n.color = ? OR n.note_text LIKE ? OR n.quote_text LIKE ?)")
        params.extend((clean_color, pattern, pattern))

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)

    with db.get_conn() as conn:
        rows = conn.execute(
            f"""
            {_SELECT_JOIN}
            {where_sql}
            ORDER BY n.updated_at DESC, n.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [dict(r) for r in rows]


def create_note(
    *,
    book_id: int,
    chapter_index: int,
    sent_id: str | None = None,
    quote_text: str = "",
    note_text: str = "",
    category: str = "quote",
    color: str = "yellow",
    replace_existing: bool = False,
    target_note_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Create a saved highlight, quote, or free-form reading note."""
    clean_quote = (quote_text or "").strip()
    clean_note = (note_text or "").strip()
    if not clean_quote and not clean_note:
        raise ValueError("Provide a quote or a note before saving.")
    clean_cat = _clean_category(category)
    clean_col = _clean_color(color, default="yellow")
    clean_sent_id = (sent_id or "").strip() or None
    now = db.utc_now_iso()

    with db.get_conn() as conn:
        book_exists = conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone()
        if book_exists is None:
            raise LookupError("Book not found")

        if replace_existing and (clean_quote or target_note_ids):
            existing_rows = conn.execute(
                """
                SELECT * FROM reader_notes
                WHERE book_id = ? AND chapter_index = ?
                ORDER BY updated_at DESC, id DESC
                """,
                (book_id, chapter_index),
            ).fetchall()
            matches = _find_matching_notes(
                existing_rows,
                clean_quote=clean_quote,
                clean_sent_id=clean_sent_id,
                target_note_ids=target_note_ids,
            )
            if matches:
                primary = next((r for r in matches if (r["note_text"] or "").strip()), matches[0])
                resolved_sent_id = primary["sent_id"] or clean_sent_id
                prim_quote = primary["quote_text"] or ""
                prim_core = _core_quote(prim_quote)
                target_core = _core_quote(clean_quote)
                resolved_quote = prim_quote
                if (
                    clean_quote
                    and "==[" not in prim_quote
                    and target_core
                    and prim_core in target_core
                    and len(target_core) > len(prim_core)
                ):
                    resolved_quote = clean_quote
                conn.execute(
                    """
                    UPDATE reader_notes
                    SET quote_text = ?, color = ?, sent_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (resolved_quote, clean_col, resolved_sent_id, now, primary["id"]),
                )
                for other in matches:
                    if other["id"] == primary["id"]:
                        continue
                    if not (other["note_text"] or "").strip():
                        conn.execute("DELETE FROM reader_notes WHERE id = ?", (other["id"],))
                    else:
                        conn.execute(
                            "UPDATE reader_notes SET color = ?, updated_at = ? WHERE id = ?",
                            (clean_col, now, other["id"]),
                        )
                row = conn.execute(f"{_SELECT_JOIN} WHERE n.id = ?", (primary["id"],)).fetchone()
                return dict(row)

        cur = conn.execute(
            """
            INSERT INTO reader_notes
                (book_id, chapter_index, sent_id, quote_text, note_text, category, color, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id,
                chapter_index,
                clean_sent_id,
                clean_quote,
                clean_note,
                clean_cat,
                clean_col,
                now,
                now,
            ),
        )
        note_id = int(cur.lastrowid)
        row = conn.execute(f"{_SELECT_JOIN} WHERE n.id = ?", (note_id,)).fetchone()
    return dict(row)


def update_note(
    note_id: int,
    *,
    quote_text: str | None = None,
    note_text: str | None = None,
    category: str | None = None,
    color: str | None = None,
) -> dict[str, Any] | None:
    """Update an existing quote, highlight color, or reading note."""
    with db.get_conn() as conn:
        existing = conn.execute("SELECT * FROM reader_notes WHERE id = ?", (note_id,)).fetchone()
        if existing is None:
            return None

        new_quote = existing["quote_text"] if quote_text is None else quote_text.strip()
        new_note = existing["note_text"] if note_text is None else note_text.strip()
        if not new_quote and not new_note:
            raise ValueError("Provide a quote or a note before saving.")
        new_cat = existing["category"] if category is None else _clean_category(category)
        existing_color = existing["color"] if "color" in existing.keys() else "yellow"
        new_col = existing_color if color is None else _clean_color(color, default=existing_color or "yellow")
        now = db.utc_now_iso()

        conn.execute(
            """
            UPDATE reader_notes
            SET quote_text = ?, note_text = ?, category = ?, color = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_quote, new_note, new_cat, new_col, now, note_id),
        )
        row = conn.execute(f"{_SELECT_JOIN} WHERE n.id = ?", (note_id,)).fetchone()
    return dict(row) if row else None


def delete_note(note_id: int) -> bool:
    """Delete a saved quote or reading note."""
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM reader_notes WHERE id = ?", (note_id,))
        return cur.rowcount > 0


def unhighlight_notes(
    *,
    book_id: int,
    chapter_index: int,
    sent_id: str | None = None,
    quote_text: str = "",
    note_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Remove highlight color from matching notes, deleting entries with no written note."""
    clean_quote = (quote_text or "").strip()
    clean_sent_id = (sent_id or "").strip() or None
    now = db.utc_now_iso()
    deleted_ids: list[int] = []
    cleared_ids: list[int] = []

    with db.get_conn() as conn:
        existing_rows = conn.execute(
            """
            SELECT * FROM reader_notes
            WHERE book_id = ? AND chapter_index = ?
            ORDER BY updated_at DESC, id DESC
            """,
            (book_id, chapter_index),
        ).fetchall()
        matches = _find_matching_notes(
            existing_rows,
            clean_quote=clean_quote,
            clean_sent_id=clean_sent_id,
            target_note_ids=note_ids,
        )
        for r in matches:
            rid = int(r["id"])
            if not (r["note_text"] or "").strip():
                conn.execute("DELETE FROM reader_notes WHERE id = ?", (rid,))
                deleted_ids.append(rid)
            else:
                conn.execute(
                    "UPDATE reader_notes SET color = '', updated_at = ? WHERE id = ?",
                    (now, rid),
                )
                cleared_ids.append(rid)

    return {
        "deleted_ids": deleted_ids,
        "cleared_ids": cleared_ids,
        "affected": len(deleted_ids) + len(cleared_ids),
    }
