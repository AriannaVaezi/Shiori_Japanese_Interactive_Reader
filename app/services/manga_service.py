"""Manga library scanning, page image serving, and SQLite-cached OCR service."""

from __future__ import annotations

import json
import posixpath
from pathlib import Path
from typing import Any

from app import config, db
from app.epub_parser import compute_file_hash
from app.manga_parser import extract_manga_toc, parse_manga, read_manga_asset
from app.services import books_service, manga_ocr_service

_MANGA_EXTENSIONS = ("*.epub", "*.cbz")


def _match_sidecar_page(
    sidecar_pages: list[dict[str, Any]],
    sidecar_by_basename: dict[str, dict[str, Any]],
    page_index: int,
    image_href: str,
) -> dict[str, Any] | None:
    if not sidecar_pages:
        return None
    base = posixpath.basename(image_href).lower()
    if base in sidecar_by_basename:
        return sidecar_by_basename[base]
    if 0 <= page_index < len(sidecar_pages):
        return sidecar_pages[page_index]
    return None


def scan_manga_folder(manga_dir: Path | None = None) -> list[dict[str, Any]]:
    """Recursively scan manga/ for .epub and .cbz volumes without disturbing novel records."""
    target_dir = manga_dir or config.MANGA_DIR
    target_dir.mkdir(parents=True, exist_ok=True)

    archive_files: list[Path] = []
    for pattern in _MANGA_EXTENSIONS:
        archive_files.extend(p for p in target_dir.rglob(pattern) if p.is_file())
    archive_files.sort(key=lambda p: str(p.relative_to(target_dir)).lower())

    resolved_paths = {str(p.resolve()) for p in archive_files}

    with db.get_conn() as conn:
        existing_rows = conn.execute(
            "SELECT id, file_path, file_hash, toc_json FROM books WHERE media_type = 'manga'"
        ).fetchall()
        existing_by_path = {row["file_path"]: row for row in existing_rows}

        for file_path, row in existing_by_path.items():
            if file_path not in resolved_paths:
                conn.execute(
                    "DELETE FROM books WHERE id = ? AND media_type = 'manga'",
                    (row["id"],),
                )

    for archive_path in archive_files:
        resolved = str(archive_path.resolve())
        file_hash = compute_file_hash(archive_path)
        existing = existing_by_path.get(resolved)
        if existing and existing["file_hash"] == file_hash:
            if existing["toc_json"] is None:
                toc_json_backfill = json.dumps(
                    extract_manga_toc(archive_path), ensure_ascii=False
                )
                with db.get_conn() as conn:
                    conn.execute(
                        "UPDATE books SET toc_json = ? WHERE id = ?",
                        (toc_json_backfill, existing["id"]),
                    )
            continue

        parsed = parse_manga(archive_path)
        now = db.utc_now_iso()
        cover_href = parsed.pages[0].image_href if parsed.pages else None
        toc_json_str = json.dumps(
            [{"title": entry.title, "page_index": entry.page_index} for entry in parsed.toc],
            ensure_ascii=False,
        )

        sidecar_by_basename: dict[str, dict[str, Any]] = {}
        for sp in parsed.sidecar_pages:
            raw_img_path = str(sp.get("img_path") or "").strip()
            if raw_img_path:
                sidecar_by_basename[posixpath.basename(raw_img_path).lower()] = sp

        with db.get_conn() as conn:
            if existing:
                book_id = int(existing["id"])
                conn.execute(
                    """
                    UPDATE books
                    SET file_hash = ?, title = ?, author = ?, cover_href = ?,
                        chapter_count = ?, media_type = 'manga', toc_json = ?
                    WHERE id = ?
                    """,
                    (
                        parsed.file_hash,
                        parsed.title,
                        parsed.author,
                        cover_href,
                        len(parsed.pages),
                        toc_json_str,
                        book_id,
                    ),
                )
                conn.execute("DELETE FROM manga_pages WHERE book_id = ?", (book_id,))
            else:
                cur = conn.execute(
                    """
                    INSERT INTO books
                        (file_path, file_hash, title, author, cover_href, chapter_count, media_type, toc_json, added_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'manga', ?, ?)
                    """,
                    (
                        parsed.file_path,
                        parsed.file_hash,
                        parsed.title,
                        parsed.author,
                        cover_href,
                        len(parsed.pages),
                        toc_json_str,
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

            for page in parsed.pages:
                ocr_json_str: str | None = None
                ocr_ts: str | None = None
                raw_sidecar = _match_sidecar_page(
                    parsed.sidecar_pages,
                    sidecar_by_basename,
                    page.page_index,
                    page.image_href,
                )
                if raw_sidecar is not None:
                    normalized_ocr = manga_ocr_service.normalize_sidecar_page(
                        raw_sidecar,
                        page_index=page.page_index,
                        fallback_w=page.width,
                        fallback_h=page.height,
                    )
                    ocr_json_str = json.dumps(normalized_ocr, ensure_ascii=False)
                    ocr_ts = now

                conn.execute(
                    """
                    INSERT INTO manga_pages
                        (book_id, page_index, image_href, width, height, is_spread, ocr_json, ocr_updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        book_id,
                        page.page_index,
                        page.image_href,
                        page.width,
                        page.height,
                        1 if page.is_spread else 0,
                        ocr_json_str,
                        ocr_ts,
                    ),
                )

    return list_manga()


def list_manga() -> list[dict[str, Any]]:
    """Return all imported manga volumes with progress and cover URL."""
    return books_service.list_books(media_type="manga")


def get_manga_detail(book_id: int) -> dict[str, Any] | None:
    """Return full metadata and ordered page descriptors for one manga volume."""
    with db.get_conn() as conn:
        row = conn.execute(
            """
            SELECT b.*, rp.chapter_index AS prog_page, rp.scroll_ratio,
                   rp.last_sent_id, rp.reading_seconds, rp.updated_at AS prog_updated_at
            FROM books b
            LEFT JOIN reading_progress rp ON rp.book_id = b.id
            WHERE b.id = ? AND b.media_type = 'manga'
            """,
            (book_id,),
        ).fetchone()
        if row is None:
            return None

        page_rows = conn.execute(
            """
            SELECT page_index, width, height, is_spread, (ocr_json IS NOT NULL) AS has_ocr
            FROM manga_pages
            WHERE book_id = ?
            ORDER BY page_index ASC
            """,
            (book_id,),
        ).fetchall()

    pages = [
        {
            "page_index": p["page_index"],
            "width": p["width"],
            "height": p["height"],
            "is_spread": bool(p["is_spread"]),
            "has_ocr": bool(p["has_ocr"]),
            "image_url": f"/api/manga/{book_id}/pages/{p['page_index']}/image",
        }
        for p in page_rows
    ]

    toc_list: list[dict[str, Any]]
    if row["toc_json"] is None:
        toc_list = extract_manga_toc(Path(row["file_path"]))
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE books SET toc_json = ? WHERE id = ?",
                (json.dumps(toc_list, ensure_ascii=False), book_id),
            )
    else:
        try:
            parsed_toc = json.loads(row["toc_json"])
            toc_list = parsed_toc if isinstance(parsed_toc, list) else []
        except Exception:
            toc_list = []

    return {
        "id": row["id"],
        "title": row["title"],
        "author": row["author"],
        "media_type": "manga",
        "chapter_count": len(pages),
        "page_count": len(pages),
        "cover_url": f"/api/manga/{book_id}/pages/0/image" if pages else None,
        "added_at": row["added_at"],
        "ocr_available": manga_ocr_service.is_ocr_available(),
        "progress": {
            "chapter_index": row["prog_page"] or 0,
            "scroll_ratio": row["scroll_ratio"] or 0.0,
            "last_sent_id": row["last_sent_id"],
            "reading_seconds": row["reading_seconds"] or 0,
            "updated_at": row["prog_updated_at"],
        },
        "toc": toc_list,
        "pages": pages,
    }


def _load_page_image_bytes(book_id: int, page_index: int) -> tuple[bytes, str] | None:
    with db.get_conn() as conn:
        row = conn.execute(
            """
            SELECT b.file_path, mp.image_href
            FROM manga_pages mp
            JOIN books b ON b.id = mp.book_id
            WHERE mp.book_id = ? AND mp.page_index = ? AND b.media_type = 'manga'
            """,
            (book_id, page_index),
        ).fetchone()
    if row is None:
        return None
    archive_path = Path(row["file_path"])
    if not archive_path.is_file():
        return None
    return read_manga_asset(archive_path, row["image_href"])


def get_manga_page_image(book_id: int, page_index: int) -> tuple[bytes, str] | None:
    """Return raw bytes and MIME type for a specific manga page image."""
    return _load_page_image_bytes(book_id, page_index)


def ocr_manga_page(book_id: int, page_index: int, force: bool = False) -> dict[str, Any] | None:
    """Return OCR blocks & SudachiPy tokens for a manga page, caching in SQLite."""
    with db.get_conn() as conn:
        row = conn.execute(
            """
            SELECT mp.id, mp.ocr_json
            FROM manga_pages mp
            JOIN books b ON b.id = mp.book_id
            WHERE mp.book_id = ? AND mp.page_index = ? AND b.media_type = 'manga'
            """,
            (book_id, page_index),
        ).fetchone()
    if row is None:
        return None

    if not force and row["ocr_json"]:
        cached_data = manga_ocr_service.ensure_blocks_tokenized(json.loads(row["ocr_json"]))
        return {
            "book_id": book_id,
            "page_index": page_index,
            "cached": True,
            **cached_data,
        }

    asset = _load_page_image_bytes(book_id, page_index)
    if asset is None:
        return None
    img_bytes, _ = asset

    ocr_data = manga_ocr_service.run_page_ocr(img_bytes, page_index)
    if ocr_data.get("ocr_available"):
        now = db.utc_now_iso()
        with db.get_conn() as conn:
            conn.execute(
                """
                UPDATE manga_pages
                SET ocr_json = ?, ocr_updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(ocr_data, ensure_ascii=False), now, row["id"]),
            )

    return {
        "book_id": book_id,
        "page_index": page_index,
        "cached": False,
        **ocr_data,
    }


def ocr_manga_crop(
    book_id: int,
    page_index: int,
    x: float,
    y: float,
    w: float,
    h: float,
) -> dict[str, Any] | None:
    """Run OCR on a user-selected normalized crop rectangle of a manga page."""
    asset = _load_page_image_bytes(book_id, page_index)
    if asset is None:
        return None
    img_bytes, _ = asset
    res = manga_ocr_service.run_crop_ocr(img_bytes, page_index, x, y, w, h)
    return {
        "book_id": book_id,
        "page_index": page_index,
        **res,
    }
