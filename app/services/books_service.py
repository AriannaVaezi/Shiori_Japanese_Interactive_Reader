"""Book library scanning, chapter tokenization caching, and reading progress."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import config, db
from app.epub_parser import compute_file_hash, parse_epub, read_epub_asset
from app.tokenizer import tokenize_chapter_html


def _substitute_book_id(html_text: str, book_id: int) -> str:
    return html_text.replace(config.BOOK_ID_PLACEHOLDER, str(book_id))


def scan_books_folder(books_dir: Path | None = None) -> list[dict[str, Any]]:
    """Scan books/ for EPUB files, importing new or modified books into SQLite."""
    target_dir = books_dir or config.BOOKS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    epub_files = sorted(target_dir.glob("*.epub"), key=lambda p: p.name.lower())
    resolved_paths = {str(p.resolve()) for p in epub_files}

    with db.get_conn() as conn:
        existing_rows = conn.execute(
            "SELECT id, file_path, file_hash FROM books"
        ).fetchall()
        existing_by_path = {row["file_path"]: row for row in existing_rows}

        # Remove books whose EPUB file was removed from books/
        for file_path, row in existing_by_path.items():
            if file_path not in resolved_paths:
                conn.execute("DELETE FROM books WHERE id = ?", (row["id"],))

    for epub_path in epub_files:
        resolved = str(epub_path.resolve())
        file_hash = compute_file_hash(epub_path)
        existing = existing_by_path.get(resolved)
        if existing and existing["file_hash"] == file_hash:
            continue

        parsed = parse_epub(epub_path)
        now = db.utc_now_iso()

        with db.get_conn() as conn:
            if existing:
                book_id = existing["id"]
                conn.execute(
                    """
                    UPDATE books
                    SET file_hash = ?, title = ?, author = ?, cover_href = ?, chapter_count = ?
                    WHERE id = ?
                    """,
                    (
                        parsed.file_hash,
                        parsed.title,
                        parsed.author,
                        parsed.cover_href,
                        len(parsed.chapters),
                        book_id,
                    ),
                )
                conn.execute("DELETE FROM chapters WHERE book_id = ?", (book_id,))
            else:
                cur = conn.execute(
                    """
                    INSERT INTO books (file_path, file_hash, title, author, cover_href, chapter_count, added_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        parsed.file_path,
                        parsed.file_hash,
                        parsed.title,
                        parsed.author,
                        parsed.cover_href,
                        len(parsed.chapters),
                        now,
                    ),
                )
                book_id = int(cur.lastrowid)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reading_progress
                        (book_id, chapter_index, scroll_ratio, last_sent_id, reading_seconds, updated_at)
                    VALUES (?, 0, 0.0, NULL, 0, ?)
                    """,
                    (book_id, now),
                )

            for ch in parsed.chapters:
                conn.execute(
                    """
                    INSERT INTO chapters
                        (book_id, chapter_index, title, spine_hrefs_json, raw_html, tokenized_html, sentences_json, char_count)
                    VALUES (?, ?, ?, ?, ?, NULL, NULL, 0)
                    """,
                    (
                        book_id,
                        ch.chapter_index,
                        ch.title,
                        json.dumps(ch.spine_hrefs, ensure_ascii=False),
                        ch.raw_html,
                    ),
                )

    return list_books()


def list_books() -> list[dict[str, Any]]:
    """Return all books with reading progress and chapter summary."""
    with db.get_conn() as conn:
        book_rows = conn.execute(
            """
            SELECT b.*, rp.chapter_index AS prog_chapter, rp.scroll_ratio,
                   rp.last_sent_id, rp.reading_seconds, rp.updated_at AS prog_updated_at
            FROM books b
            LEFT JOIN reading_progress rp ON rp.book_id = b.id
            ORDER BY b.title COLLATE NOCASE ASC
            """
        ).fetchall()

        chapters_rows = conn.execute(
            "SELECT book_id, chapter_index, title FROM chapters ORDER BY book_id, chapter_index"
        ).fetchall()

    chapters_by_book: dict[int, list[dict[str, Any]]] = {}
    for ch in chapters_rows:
        chapters_by_book.setdefault(ch["book_id"], []).append(
            {"chapter_index": ch["chapter_index"], "title": ch["title"]}
        )

    result: list[dict[str, Any]] = []
    for row in book_rows:
        book_id = row["id"]
        cover_url = (
            f"/api/books/{book_id}/assets/{row['cover_href']}"
            if row["cover_href"]
            else None
        )
        result.append(
            {
                "id": book_id,
                "title": row["title"],
                "author": row["author"],
                "chapter_count": row["chapter_count"],
                "cover_url": cover_url,
                "added_at": row["added_at"],
                "progress": {
                    "chapter_index": row["prog_chapter"] or 0,
                    "scroll_ratio": row["scroll_ratio"] or 0.0,
                    "last_sent_id": row["last_sent_id"],
                    "reading_seconds": row["reading_seconds"] or 0,
                    "updated_at": row["prog_updated_at"],
                },
                "chapters": chapters_by_book.get(book_id, []),
            }
        )
    return result


def get_book_detail(book_id: int) -> dict[str, Any] | None:
    """Return full details for one book or None if not found."""
    for book in list_books():
        if book["id"] == book_id:
            return book
    return None


def get_chapter(book_id: int, chapter_index: int) -> dict[str, Any] | None:
    """Return tokenized HTML and metadata for a chapter, tokenizing lazily on first open."""
    with db.get_conn() as conn:
        book_row = conn.execute(
            "SELECT id, title, chapter_count FROM books WHERE id = ?",
            (book_id,),
        ).fetchone()
        if book_row is None:
            return None

        ch_row = conn.execute(
            """
            SELECT id, chapter_index, title, raw_html, tokenized_html, sentences_json, char_count
            FROM chapters
            WHERE book_id = ? AND chapter_index = ?
            """,
            (book_id, chapter_index),
        ).fetchone()
        if ch_row is None:
            return None

        tokenized_html = ch_row["tokenized_html"]
        char_count = ch_row["char_count"] or 0

        if tokenized_html is None:
            tokenized_html, sentences, char_count = tokenize_chapter_html(
                ch_row["raw_html"], chapter_index
            )
            conn.execute(
                """
                UPDATE chapters
                SET tokenized_html = ?, sentences_json = ?, char_count = ?
                WHERE id = ?
                """,
                (
                    tokenized_html,
                    json.dumps(sentences, ensure_ascii=False),
                    char_count,
                    ch_row["id"],
                ),
            )

        grammar_rows = conn.execute(
            "SELECT DISTINCT sent_id FROM grammar_threads WHERE book_id = ? AND chapter_index = ?",
            (book_id, chapter_index),
        ).fetchall()
        grammar_sent_ids = [r["sent_id"] for r in grammar_rows]

        sticky_rows = conn.execute(
            "SELECT item_key FROM trouble_items WHERE item_type = 'word' AND retired = 0 AND trouble_score >= 3.0"
        ).fetchall()
        sticky_keys = [r["item_key"] for r in sticky_rows]

        trouble_rows = conn.execute(
            "SELECT item_key FROM trouble_items WHERE item_type = 'word' AND retired = 0 AND trouble_score > 0.0"
        ).fetchall()
        trouble_keys = [r["item_key"] for r in trouble_rows]

        chapter_trouble_count = conn.execute(
            """
            SELECT COUNT(*) FROM trouble_items
            WHERE retired = 0 AND (book_id = ? OR book_id IS NULL)
            """,
            (book_id,),
        ).fetchone()[0]

    return {
        "book_id": book_id,
        "book_title": book_row["title"],
        "chapter_index": ch_row["chapter_index"],
        "total_chapters": book_row["chapter_count"],
        "title": ch_row["title"],
        "html": _substitute_book_id(tokenized_html, book_id),
        "char_count": char_count,
        "grammar_sent_ids": grammar_sent_ids,
        "sticky_keys": sticky_keys,
        "trouble_keys": trouble_keys,
        "chapter_trouble_count": chapter_trouble_count,
    }


def save_progress(
    book_id: int,
    chapter_index: int,
    scroll_ratio: float,
    last_sent_id: str | None = None,
    reading_seconds_delta: int = 0,
) -> dict[str, Any] | None:
    """Update reading progress for a book."""
    now = db.utc_now_iso()
    clamped_scroll = max(0.0, min(1.0, float(scroll_ratio)))
    delta = max(0, int(reading_seconds_delta))

    with db.get_conn() as conn:
        exists = conn.execute("SELECT 1 FROM books WHERE id = ?", (book_id,)).fetchone()
        if not exists:
            return None

        conn.execute(
            """
            INSERT INTO reading_progress
                (book_id, chapter_index, scroll_ratio, last_sent_id, reading_seconds, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(book_id) DO UPDATE SET
                chapter_index = excluded.chapter_index,
                scroll_ratio = excluded.scroll_ratio,
                last_sent_id = COALESCE(excluded.last_sent_id, reading_progress.last_sent_id),
                reading_seconds = reading_progress.reading_seconds + excluded.reading_seconds,
                updated_at = excluded.updated_at
            """,
            (book_id, chapter_index, clamped_scroll, last_sent_id, delta, now),
        )
        row = conn.execute(
            "SELECT * FROM reading_progress WHERE book_id = ?",
            (book_id,),
        ).fetchone()

    return dict(row) if row else None


def get_book_asset(book_id: int, asset_path: str) -> tuple[bytes, str] | None:
    """Read an embedded image or asset from the book's EPUB archive."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT file_path FROM books WHERE id = ?",
            (book_id,),
        ).fetchone()
    if row is None:
        return None
    epub_path = Path(row["file_path"])
    if not epub_path.is_file():
        return None
    return read_epub_asset(epub_path, asset_path)
