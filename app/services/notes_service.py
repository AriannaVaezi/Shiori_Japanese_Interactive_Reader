"""Saved book passages, quotes, and free-form study notes service."""

from __future__ import annotations

from typing import Any

from app import db

ALLOWED_CATEGORIES = {"quote", "note", "expression"}

_SELECT_JOIN = """
SELECT n.*,
       b.title AS book_title,
       c.title AS chapter_title
FROM reader_notes n
JOIN books b ON b.id = n.book_id
LEFT JOIN chapters c ON c.book_id = n.book_id AND c.chapter_index = n.chapter_index
"""


def _clean_category(category: str | None) -> str:
    val = (category or "quote").strip().lower()
    return val if val in ALLOWED_CATEGORIES else "quote"


def list_notes(
    *,
    book_id: int | None = None,
    chapter_index: int | None = None,
    category: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return saved quotes and notes filtered optionally by book, chapter, or category."""
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
) -> dict[str, Any]:
    """Create a saved quote or free-form reading note."""
    clean_quote = (quote_text or "").strip()
    clean_note = (note_text or "").strip()
    if not clean_quote and not clean_note:
        raise ValueError("Provide a quote or a note before saving.")
    clean_cat = _clean_category(category)
    clean_sent_id = (sent_id or "").strip() or None
    now = db.utc_now_iso()

    with db.get_conn() as conn:
        book_exists = conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone()
        if book_exists is None:
            raise LookupError("Book not found")

        cur = conn.execute(
            """
            INSERT INTO reader_notes
                (book_id, chapter_index, sent_id, quote_text, note_text, category, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                book_id,
                chapter_index,
                clean_sent_id,
                clean_quote,
                clean_note,
                clean_cat,
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
) -> dict[str, Any] | None:
    """Update an existing quote or reading note."""
    with db.get_conn() as conn:
        existing = conn.execute("SELECT * FROM reader_notes WHERE id = ?", (note_id,)).fetchone()
        if existing is None:
            return None

        new_quote = existing["quote_text"] if quote_text is None else quote_text.strip()
        new_note = existing["note_text"] if note_text is None else note_text.strip()
        if not new_quote and not new_note:
            raise ValueError("Provide a quote or a note before saving.")
        new_cat = existing["category"] if category is None else _clean_category(category)
        now = db.utc_now_iso()

        conn.execute(
            """
            UPDATE reader_notes
            SET quote_text = ?, note_text = ?, category = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_quote, new_note, new_cat, now, note_id),
        )
        row = conn.execute(f"{_SELECT_JOIN} WHERE n.id = ?", (note_id,)).fetchone()
    return dict(row) if row else None


def delete_note(note_id: int) -> bool:
    """Delete a saved quote or reading note."""
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM reader_notes WHERE id = ?", (note_id,))
        return cur.rowcount > 0
