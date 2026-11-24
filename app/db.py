"""SQLite database initialization, connection handling, and shared queries."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from app import config

_DB_LOCK = threading.RLock()
_ACTIVE_DB_PATH: Path | None = None


def utc_now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def get_db_path() -> Path:
    """Return the active SQLite database path."""
    return _ACTIVE_DB_PATH or config.DB_PATH


def set_db_path(path: Path | None) -> None:
    """Override the SQLite database path (primarily for testing)."""
    global _ACTIVE_DB_PATH
    _ACTIVE_DB_PATH = path


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a thread-safe SQLite connection configured with WAL and foreign keys."""
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _DB_LOCK:
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


def init_db() -> None:
    """Create database tables and seed default settings if missing."""
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                file_hash TEXT NOT NULL,
                title TEXT NOT NULL,
                author TEXT NOT NULL,
                cover_href TEXT,
                chapter_count INTEGER DEFAULT 0,
                media_type TEXT NOT NULL DEFAULT 'novel',
                added_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS manga_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                page_index INTEGER NOT NULL,
                image_href TEXT NOT NULL,
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                is_spread INTEGER NOT NULL DEFAULT 0,
                ocr_json TEXT,
                ocr_updated_at TEXT,
                UNIQUE(book_id, page_index)
            );

            CREATE TABLE IF NOT EXISTS chapters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                chapter_index INTEGER NOT NULL,
                title TEXT NOT NULL,
                spine_hrefs_json TEXT NOT NULL,
                raw_html TEXT NOT NULL,
                tokenized_html TEXT,
                sentences_json TEXT,
                char_count INTEGER DEFAULT 0,
                UNIQUE(book_id, chapter_index)
            );

            CREATE TABLE IF NOT EXISTS reading_progress (
                book_id INTEGER PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE,
                chapter_index INTEGER NOT NULL DEFAULT 0,
                scroll_ratio REAL NOT NULL DEFAULT 0.0,
                last_sent_id TEXT,
                reading_seconds INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dict_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lemma TEXT NOT NULL,
                reading TEXT NOT NULL,
                jj_json TEXT,
                en_json TEXT,
                provider TEXT,
                model TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(lemma, reading)
            );

            CREATE TABLE IF NOT EXISTS lookup_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER,
                chapter_index INTEGER,
                sent_id TEXT,
                lemma TEXT NOT NULL,
                surface TEXT NOT NULL,
                reading TEXT NOT NULL,
                revealed_english INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trouble_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_type TEXT NOT NULL,
                item_key TEXT NOT NULL,
                display_text TEXT NOT NULL,
                reading TEXT,
                book_id INTEGER,
                last_chapter_index INTEGER,
                sent_id TEXT,
                sent_text TEXT,
                jj_views INTEGER NOT NULL DEFAULT 0,
                en_reveals INTEGER NOT NULL DEFAULT 0,
                grammar_asks INTEGER NOT NULL DEFAULT 0,
                trouble_score REAL NOT NULL DEFAULT 0.0,
                correct_count INTEGER NOT NULL DEFAULT 0,
                wrong_count INTEGER NOT NULL DEFAULT 0,
                retired INTEGER NOT NULL DEFAULT 0,
                memory_stage INTEGER NOT NULL DEFAULT 0,
                next_review_after TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(item_type, item_key)
            );

            CREATE TABLE IF NOT EXISTS quiz_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER,
                chapter_index INTEGER,
                trigger_type TEXT NOT NULL,
                total_questions INTEGER NOT NULL,
                correct_count INTEGER NOT NULL,
                questions_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS grammar_threads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL,
                chapter_index INTEGER NOT NULL,
                sent_id TEXT NOT NULL,
                target_text TEXT NOT NULL DEFAULT '',
                sentence_text TEXT NOT NULL,
                context_text TEXT NOT NULL,
                messages_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(book_id, chapter_index, sent_id, target_text)
            );

            CREATE TABLE IF NOT EXISTS reader_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                chapter_index INTEGER NOT NULL,
                sent_id TEXT,
                quote_text TEXT NOT NULL DEFAULT '',
                note_text TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT 'quote',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

        existing_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(trouble_items)").fetchall()
        }
        for col_name, col_def in (
            ("correct_count", "INTEGER NOT NULL DEFAULT 0"),
            ("wrong_count", "INTEGER NOT NULL DEFAULT 0"),
            ("retired", "INTEGER NOT NULL DEFAULT 0"),
            ("memory_stage", "INTEGER NOT NULL DEFAULT 0"),
            ("next_review_after", "TEXT"),
        ):
            if col_name not in existing_cols:
                conn.execute(f"ALTER TABLE trouble_items ADD COLUMN {col_name} {col_def}")

        book_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(books)").fetchall()
        }
        if "media_type" not in book_cols:
            conn.execute("ALTER TABLE books ADD COLUMN media_type TEXT NOT NULL DEFAULT 'novel'")

        for key, default_val in config.DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, default_val),
            )


def get_all_settings() -> dict[str, str]:
    """Return all settings merged with defaults."""
    settings = dict(config.DEFAULT_SETTINGS)
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        for row in rows:
            settings[row["key"]] = row["value"]
    return settings


def update_settings(updates: dict[str, str]) -> dict[str, str]:
    """Persist allowed setting keys and return updated settings."""
    with get_conn() as conn:
        for key, value in updates.items():
            if key in config.ALLOWED_SETTING_KEYS:
                conn.execute(
                    """
                    INSERT INTO settings (key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (key, str(value)),
                )
    return get_all_settings()


def record_trouble_event(
    *,
    item_type: str,
    item_key: str,
    display_text: str,
    reading: str = "",
    book_id: int | None = None,
    chapter_index: int | None = None,
    sent_id: str | None = None,
    sent_text: str | None = None,
    jj_delta: int = 0,
    en_delta: int = 0,
    grammar_delta: int = 0,
    score_delta: float = 0.0,
) -> None:
    """Upsert an item into trouble_items and increment its difficulty signals."""
    now = utc_now_iso()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT last_chapter_index, jj_views, en_reveals FROM trouble_items WHERE item_type = ? AND item_key = ?",
            (item_type, item_key),
        ).fetchone()

        # Sticky boost when the learner encounters trouble across different chapters
        effective_score_delta = score_delta
        if (
            existing is not None
            and chapter_index is not None
            and existing["last_chapter_index"] is not None
            and existing["last_chapter_index"] != chapter_index
        ):
            effective_score_delta += 1.0

        conn.execute(
            """
            INSERT INTO trouble_items (
                item_type, item_key, display_text, reading, book_id,
                last_chapter_index, sent_id, sent_text,
                jj_views, en_reveals, grammar_asks, trouble_score, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(item_type, item_key) DO UPDATE SET
                display_text = COALESCE(NULLIF(excluded.display_text, ''), trouble_items.display_text),
                reading = COALESCE(NULLIF(excluded.reading, ''), trouble_items.reading),
                book_id = COALESCE(excluded.book_id, trouble_items.book_id),
                last_chapter_index = COALESCE(excluded.last_chapter_index, trouble_items.last_chapter_index),
                sent_id = COALESCE(NULLIF(excluded.sent_id, ''), trouble_items.sent_id),
                sent_text = COALESCE(NULLIF(excluded.sent_text, ''), trouble_items.sent_text),
                jj_views = trouble_items.jj_views + excluded.jj_views,
                en_reveals = trouble_items.en_reveals + excluded.en_reveals,
                grammar_asks = trouble_items.grammar_asks + excluded.grammar_asks,
                trouble_score = trouble_items.trouble_score + ?,
                retired = 0,
                memory_stage = 0,
                next_review_after = NULL,
                updated_at = excluded.updated_at
            """,
            (
                item_type,
                item_key,
                display_text,
                reading,
                book_id,
                chapter_index,
                sent_id,
                sent_text,
                jj_delta,
                en_delta,
                grammar_delta,
                effective_score_delta,
                now,
                effective_score_delta,
            ),
        )


def set_trouble_retired(item_id: int, retired: bool) -> dict | None:
    """Toggle the retired status of a trouble item."""
    now = utc_now_iso()
    new_stage = 3 if retired else 0
    with get_conn() as conn:
        cur = conn.execute(
            """
            UPDATE trouble_items
            SET retired = ?, memory_stage = ?, next_review_after = NULL, updated_at = ?
            WHERE id = ?
            """,
            (1 if retired else 0, new_stage, now, item_id),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM trouble_items WHERE id = ?", (item_id,)).fetchone()
    return dict(row) if row else None


def _parse_iso_utc(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def apply_quiz_item_result(
    item_id: int,
    correct: bool,
    *,
    now_dt: datetime | None = None,
) -> dict | None:
    """Update trouble item score and multi-stage cool-down spacing after a quiz answer."""
    cur_dt = (now_dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    now = cur_dt.isoformat()
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM trouble_items WHERE id = ?", (item_id,)).fetchone()
        if row is None:
            return None
        cur_score = float(row["trouble_score"] or 0.0)
        cur_stage = int(row["memory_stage"] or 0)
        next_due_dt = _parse_iso_utc(row["next_review_after"])
        cooled_down = next_due_dt is None or cur_dt >= next_due_dt

        if correct:
            new_score = max(0.0, cur_score - 1.5)
            new_retired = 0
            new_next_review: str | None = row["next_review_after"]

            if cur_stage <= 0:
                new_stage = 1
                new_next_review = (cur_dt + timedelta(hours=20)).isoformat()
            elif cur_stage == 1:
                if cooled_down:
                    new_stage = 2
                    new_next_review = (cur_dt + timedelta(hours=72)).isoformat()
                else:
                    new_stage = 1
                    new_next_review = (cur_dt + timedelta(hours=20)).isoformat()
            else:
                if cooled_down:
                    new_stage = 3
                    new_next_review = None
                    new_score = 0.0
                    new_retired = 1
                else:
                    new_stage = cur_stage

            conn.execute(
                """
                UPDATE trouble_items
                SET correct_count = correct_count + 1,
                    trouble_score = ?,
                    memory_stage = ?,
                    next_review_after = ?,
                    retired = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (new_score, new_stage, new_next_review, new_retired, now, item_id),
            )
        else:
            new_score = cur_score + 0.5
            conn.execute(
                """
                UPDATE trouble_items
                SET wrong_count = wrong_count + 1,
                    trouble_score = ?,
                    memory_stage = 0,
                    next_review_after = NULL,
                    retired = 0,
                    updated_at = ?
                WHERE id = ?
                """,
                (new_score, now, item_id),
            )
        updated = conn.execute("SELECT * FROM trouble_items WHERE id = ?", (item_id,)).fetchone()
    return dict(updated) if updated else None
