"""Manga archive parsing (.epub and .cbz) and safe page image streaming."""

from __future__ import annotations

import json
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup
from PIL import Image

from app.epub_parser import (
    _OPF_NS,
    _find_opf_path,
    _norm_zip_path,
    compute_file_hash,
    read_epub_asset,
)

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_NATURAL_TOKEN_RE = re.compile(r"(\d+)")


@dataclass
class ParsedMangaPage:
    page_index: int
    image_href: str
    width: int
    height: int
    is_spread: bool


@dataclass
class ParsedManga:
    file_path: str
    file_hash: str
    title: str
    author: str
    page_progression: str
    pages: list[ParsedMangaPage]
    sidecar_pages: list[dict[str, Any]] = field(default_factory=list)


def _natural_sort_key(path_str: str) -> list[tuple[int, int | str]]:
    """Sort key splitting embedded integers for natural page ordering."""
    parts = _NATURAL_TOKEN_RE.split(path_str.lower())
    out: list[tuple[int, int | str]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            out.append((0, int(part)))
        else:
            out.append((1, part))
    return out


def _is_image_member(name: str) -> bool:
    """Return True if zip member is a non-hidden supported manga image."""
    clean = name.replace("\\", "/").lstrip("/")
    if not clean or clean.endswith("/") or clean.startswith("__MACOSX/"):
        return False
    base = posixpath.basename(clean)
    if base.startswith("."):
        return False
    return posixpath.splitext(base)[1].lower() in _IMAGE_EXTS


def _extract_images_from_xhtml(
    raw_bytes: bytes,
    spine_href: str,
    names_set: set[str],
    lower_map: dict[str, str],
) -> list[str]:
    """Extract referenced image zip paths in document order from a spine XHTML file."""
    soup = BeautifulSoup(raw_bytes, "lxml-xml")
    if soup.find(["image", "img"]) is None:
        soup = BeautifulSoup(raw_bytes, "lxml")

    spine_dir = posixpath.dirname(spine_href)
    found: list[str] = []

    for tag in soup.find_all(["image", "img"]):
        if tag.name == "image":
            raw_ref = (
                tag.get("xlink:href")
                or tag.get("href")
                or tag.get("{http://www.w3.org/1999/xlink}href")
            )
        else:
            raw_ref = tag.get("src")

        if not raw_ref:
            continue
        unquoted = unquote(str(raw_ref))
        candidate = _norm_zip_path(spine_dir, unquoted)
        if not candidate:
            continue

        resolved: str | None = None
        if candidate in names_set:
            resolved = candidate
        elif candidate.lower() in lower_map:
            resolved = lower_map[candidate.lower()]

        if resolved and posixpath.splitext(resolved)[1].lower() in _IMAGE_EXTS:
            if not found or found[-1] != resolved:
                found.append(resolved)

    return found


def _load_sidecar_mokuro(archive_path: Path) -> list[dict[str, Any]]:
    """Load optional sibling .mokuro JSON sidecar pages if present and valid."""
    candidates = [
        archive_path.with_suffix(".mokuro"),
        archive_path.parent / f"{archive_path.stem}.mokuro",
    ]
    for sidecar_path in candidates:
        if not sidecar_path.is_file():
            continue
        try:
            data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        pages = data.get("pages") if isinstance(data, dict) else None
        if isinstance(pages, list):
            return [p for p in pages if isinstance(p, dict)]
    return []


def _parse_epub_manga(zf: zipfile.ZipFile, archive_path: Path) -> tuple[str, str, str, list[str]]:
    """Parse manga EPUB metadata and ordered image hrefs from OPF spine."""
    names = zf.namelist()
    names_set = set(names)
    lower_map = {n.lower(): n for n in names}

    title = archive_path.stem
    author = ""
    page_progression = "rtl"
    ordered_hrefs: list[str] = []

    try:
        opf_path = _find_opf_path(zf)
        opf_dir = posixpath.dirname(opf_path)
        opf_root = ET.fromstring(zf.read(opf_path))

        title_el = opf_root.find(".//dc:title", _OPF_NS)
        author_el = opf_root.find(".//dc:creator", _OPF_NS)
        if title_el is not None and (title_el.text or "").strip():
            title = title_el.text.strip()
        if author_el is not None and (author_el.text or "").strip():
            author = author_el.text.strip()

        manifest: dict[str, tuple[str, str]] = {}
        for item in opf_root.findall(".//opf:manifest/opf:item", _OPF_NS):
            item_id = item.attrib.get("id", "")
            href = item.attrib.get("href", "")
            media_type = item.attrib.get("media-type", "")
            if item_id and href:
                manifest[item_id] = (_norm_zip_path(opf_dir, unquote(href)), media_type)

        spine_el = opf_root.find(".//opf:spine", _OPF_NS)
        if spine_el is not None:
            prog = (spine_el.attrib.get("page-progression-direction") or "").strip().lower()
            if prog in {"rtl", "ltr"}:
                page_progression = prog

            seen_hrefs: set[str] = set()
            for itemref in spine_el.findall("./opf:itemref", _OPF_NS):
                idref = itemref.attrib.get("idref", "")
                if idref not in manifest:
                    continue
                zip_href, media_type = manifest[idref]
                if zip_href not in names_set and zip_href.lower() in lower_map:
                    zip_href = lower_map[zip_href.lower()]
                if zip_href not in names_set:
                    continue

                if media_type.startswith("image/") or posixpath.splitext(zip_href)[1].lower() in _IMAGE_EXTS:
                    if zip_href not in seen_hrefs:
                        seen_hrefs.add(zip_href)
                        ordered_hrefs.append(zip_href)
                    continue

                if media_type in ("application/xhtml+xml", "text/html", ""):
                    raw_bytes = zf.read(zip_href)
                    for img_href in _extract_images_from_xhtml(raw_bytes, zip_href, names_set, lower_map):
                        if img_href not in seen_hrefs:
                            seen_hrefs.add(img_href)
                            ordered_hrefs.append(img_href)
    except Exception:
        ordered_hrefs = []

    # Fallback: naturally sorted image files in archive if spine yielded none
    if not ordered_hrefs:
        ordered_hrefs = sorted(
            [n for n in names if _is_image_member(n)],
            key=_natural_sort_key,
        )

    return title, author, page_progression, ordered_hrefs


def _parse_cbz_manga(zf: zipfile.ZipFile, archive_path: Path) -> tuple[str, str, str, list[str]]:
    """Parse CBZ/ZIP manga archive metadata and naturally sorted image hrefs."""
    ordered_hrefs = sorted(
        [n for n in zf.namelist() if _is_image_member(n)],
        key=_natural_sort_key,
    )
    return archive_path.stem, "", "rtl", ordered_hrefs


def parse_manga(archive_path: Path) -> ParsedManga:
    """Parse an EPUB or CBZ manga volume into ordered page records with exact dimensions."""
    file_hash = compute_file_hash(archive_path)
    ext = archive_path.suffix.lower()

    with zipfile.ZipFile(archive_path, "r") as zf:
        if ext == ".epub":
            title, author, page_progression, ordered_hrefs = _parse_epub_manga(zf, archive_path)
        else:
            title, author, page_progression, ordered_hrefs = _parse_cbz_manga(zf, archive_path)

        pages: list[ParsedMangaPage] = []
        for idx, href in enumerate(ordered_hrefs):
            width = 0
            height = 0
            try:
                with zf.open(href) as img_stream:
                    with Image.open(img_stream) as im:
                        width, height = im.size
            except Exception:
                width, height = 0, 0
            is_spread = bool(width > 0 and height > 0 and width > height * 1.15)
            pages.append(
                ParsedMangaPage(
                    page_index=idx,
                    image_href=href,
                    width=int(width),
                    height=int(height),
                    is_spread=is_spread,
                )
            )

    sidecar_pages = _load_sidecar_mokuro(archive_path)

    return ParsedManga(
        file_path=str(archive_path.resolve()),
        file_hash=file_hash,
        title=title or archive_path.stem,
        author=author,
        page_progression=page_progression,
        pages=pages,
        sidecar_pages=sidecar_pages,
    )


def read_manga_asset(archive_path: str | Path, inner_path: str) -> tuple[bytes, str] | None:
    """Read an embedded page image safely from a manga EPUB/CBZ archive."""
    res = read_epub_asset(Path(archive_path), inner_path)
    if res is None:
        return None
    data, mime = res
    if not mime or mime == "application/octet-stream":
        mime = "image/jpeg"
    return data, mime
