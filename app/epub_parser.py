"""EPUB parsing, logical chapter grouping, and safe asset streaming."""

from __future__ import annotations

import hashlib
import mimetypes
import posixpath
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup, Tag

from app import config

_CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
_OPF_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
_NCX_NS = {"ncx": "http://www.daisy.org/z3986/2005/ncx/"}
_XHTML_NS = {"x": "http://www.w3.org/1999/xhtml", "epub": "http://www.idpf.org/2007/ops"}

_MIME_OVERRIDES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".xhtml": "application/xhtml+xml",
    ".html": "text/html",
}


@dataclass
class ParsedChapter:
    chapter_index: int
    title: str
    spine_hrefs: list[str]
    raw_html: str


@dataclass
class ParsedBook:
    file_path: str
    file_hash: str
    title: str
    author: str
    cover_href: str | None
    chapters: list[ParsedChapter]


@dataclass
class _SpinePart:
    href: str
    body_html: str
    heading_text: str
    toc_label: str | None
    first_image_href: str | None


def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of a file in chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm_zip_path(base_dir: str, rel_href: str) -> str:
    """Resolve a relative href inside the EPUB archive into a normalized zip path."""
    clean = rel_href.split("#", 1)[0].split("?", 1)[0].strip()
    if not clean:
        return ""
    joined = posixpath.join(base_dir, clean) if base_dir else clean
    normed = posixpath.normpath(joined)
    return normed.lstrip("/")


def _find_opf_path(zf: zipfile.ZipFile) -> str:
    container_bytes = zf.read("META-INF/container.xml")
    root = ET.fromstring(container_bytes)
    rootfile = root.find(".//c:rootfile", _CONTAINER_NS)
    if rootfile is None:
        rootfile = root.find(".//{*}rootfile")
    if rootfile is None or not rootfile.attrib.get("full-path"):
        raise ValueError("Invalid EPUB: missing META-INF/container.xml rootfile.")
    return rootfile.attrib["full-path"]


def _parse_ncx_toc(zf: zipfile.ZipFile, ncx_href: str) -> dict[str, str]:
    toc_map: dict[str, str] = {}
    try:
        ncx_root = ET.fromstring(zf.read(ncx_href))
    except (KeyError, ET.ParseError):
        return toc_map

    ncx_dir = posixpath.dirname(ncx_href)
    for nav_point in ncx_root.findall(".//ncx:navPoint", _NCX_NS):
        label_el = nav_point.find("./ncx:navLabel/ncx:text", _NCX_NS)
        content_el = nav_point.find("./ncx:content", _NCX_NS)
        if content_el is None:
            continue
        src = content_el.attrib.get("src", "")
        target = _norm_zip_path(ncx_dir, src)
        label = (label_el.text or "").strip() if label_el is not None else ""
        if target and label and target not in toc_map:
            toc_map[target] = label
    return toc_map


def _parse_nav_toc(zf: zipfile.ZipFile, nav_href: str) -> dict[str, str]:
    toc_map: dict[str, str] = {}
    try:
        soup = BeautifulSoup(zf.read(nav_href), "lxml-xml")
    except KeyError:
        return toc_map

    nav_dir = posixpath.dirname(nav_href)
    nav_el = None
    for nav in soup.find_all("nav"):
        epub_type = nav.get("epub:type") or nav.get("type") or ""
        if "toc" in str(epub_type).lower():
            nav_el = nav
            break
    if nav_el is None:
        nav_el = soup.find("nav")
    if nav_el is None:
        return toc_map

    for a in nav_el.find_all("a", href=True):
        target = _norm_zip_path(nav_dir, str(a["href"]))
        label = a.get_text(" ", strip=True)
        if target and label and target not in toc_map:
            toc_map[target] = label
    return toc_map


def _extract_heading_text(body: Tag) -> str:
    for tag_name in ("h1", "h2", "h3"):
        heading = body.find(tag_name)
        if heading is None:
            continue
        clone = BeautifulSoup(str(heading), "lxml")
        for rt in clone.find_all(["rt", "rp"]):
            rt.decompose()
        text = clone.get_text("", strip=True)
        if text:
            return text
    return ""


def _build_asset_url(zip_href: str, book_id_placeholder: str) -> str:
    return f"/api/books/{book_id_placeholder}/assets/{zip_href}"


def _rewrite_body_html(
    raw_bytes: bytes,
    spine_href: str,
    book_id_placeholder: str,
) -> tuple[str, str, str | None]:
    """Parse a spine XHTML body, rewrite image/SVG links, and detect headings."""
    soup = BeautifulSoup(raw_bytes, "lxml-xml")
    body = soup.find("body")
    if body is None:
        soup = BeautifulSoup(raw_bytes, "lxml")
        body = soup.find("body")
    if body is None:
        return "", "", None

    spine_dir = posixpath.dirname(spine_href)
    first_image_href: str | None = None

    for tag in body.find_all(["script", "style"]):
        tag.decompose()

    # Convert SVG wrappers containing <image> (e.g., covers and full-page art) to standard <img>
    for svg in list(body.find_all("svg")):
        img_el = svg.find("image")
        if img_el is None:
            svg.decompose()
            continue
        href = (
            img_el.get("xlink:href")
            or img_el.get("href")
            or img_el.get("{http://www.w3.org/1999/xlink}href")
        )
        if not href:
            svg.decompose()
            continue
        zip_href = _norm_zip_path(spine_dir, str(href))
        if first_image_href is None:
            first_image_href = zip_href
        figure = soup.new_tag("figure", attrs={"class": "epub-illust"})
        new_img = soup.new_tag(
            "img",
            attrs={
                "src": _build_asset_url(zip_href, book_id_placeholder),
                "loading": "lazy",
                "alt": "",
            },
        )
        figure.append(new_img)
        parent = svg.parent
        if parent and parent.name == "div" and not parent.get_text(strip=True):
            parent.replace_with(figure)
        else:
            svg.replace_with(figure)

    # Rewrite standard <img> tags
    for img in list(body.find_all("img")):
        src = img.get("src")
        if not src:
            img.decompose()
            continue
        zip_href = _norm_zip_path(spine_dir, str(src))
        if first_image_href is None:
            first_image_href = zip_href
        img["src"] = _build_asset_url(zip_href, book_id_placeholder)
        img["loading"] = "lazy"
        img.attrs.pop("width", None)
        img.attrs.pop("height", None)

        parent = img.parent
        if (
            parent
            and parent.name == "div"
            and len(parent.find_all(["p", "h1", "h2", "h3", "img"])) == 1
            and not parent.get_text(strip=True)
        ):
            figure = soup.new_tag("figure", attrs={"class": "epub-illust"})
            figure.append(img.extract())
            parent.replace_with(figure)

    heading_text = _extract_heading_text(body)
    inner_html = "".join(str(child) for child in body.contents).strip()
    return inner_html, heading_text, first_image_href


def _group_spine_into_chapters(
    parts: list[_SpinePart],
    toc_map: dict[str, str],
) -> list[ParsedChapter]:
    """Group raw spine XHTML parts into logical chapters."""
    if not parts:
        return []

    toc_hits = sum(1 for p in parts if p.toc_label)
    heading_hits = sum(1 for p in parts if p.heading_text)

    if toc_hits >= 3:
        split_mode = "toc"
    elif heading_hits >= 1:
        split_mode = "heading"
    else:
        split_mode = "per_spine"

    chapters: list[ParsedChapter] = []
    current_parts: list[_SpinePart] = []
    current_title = ""

    def flush_chapter() -> None:
        if not current_parts:
            return
        combined_html = "\n".join(
            f'<section class="epub-spine-part">{p.body_html}</section>'
            for p in current_parts
            if p.body_html
        )
        if not combined_html.strip():
            return
        idx = len(chapters)
        title = current_title or f"セクション {idx + 1}"
        chapters.append(
            ParsedChapter(
                chapter_index=idx,
                title=title,
                spine_hrefs=[p.href for p in current_parts],
                raw_html=combined_html,
            )
        )

    for idx, part in enumerate(parts):
        if split_mode == "per_spine":
            current_parts = [part]
            current_title = part.heading_text or part.toc_label or f"パート {idx + 1}"
            flush_chapter()
            current_parts = []
            current_title = ""
            continue

        should_split = False
        new_title = ""
        if split_mode == "toc":
            if part.toc_label:
                should_split = True
                new_title = part.heading_text or part.toc_label
        else:
            # Heading-driven grouping (handles Calibre/Kodansha EPUBs with sparse NCX)
            if part.heading_text:
                should_split = True
                new_title = part.heading_text
            elif part.toc_label and part.toc_label != "表紙":
                should_split = True
                new_title = part.toc_label

        if should_split and current_parts:
            flush_chapter()
            current_parts = []

        if should_split or not current_parts:
            if not current_title or should_split:
                if new_title:
                    current_title = new_title
                elif idx == 0:
                    current_title = "表紙・まえがき"

        current_parts.append(part)

    flush_chapter()
    return chapters


def parse_epub(
    epub_path: Path,
    book_id_placeholder: str = config.BOOK_ID_PLACEHOLDER,
) -> ParsedBook:
    """Parse an EPUB file into metadata, cover reference, and grouped chapters."""
    file_hash = compute_file_hash(epub_path)

    with zipfile.ZipFile(epub_path, "r") as zf:
        opf_path = _find_opf_path(zf)
        opf_dir = posixpath.dirname(opf_path)
        opf_root = ET.fromstring(zf.read(opf_path))

        title_el = opf_root.find(".//dc:title", _OPF_NS)
        author_el = opf_root.find(".//dc:creator", _OPF_NS)
        title = (title_el.text or "").strip() if title_el is not None else epub_path.stem
        author = (author_el.text or "").strip() if author_el is not None else "不明"

        manifest: dict[str, tuple[str, str, str]] = {}
        cover_id: str | None = None
        ncx_href: str | None = None
        nav_href: str | None = None

        for meta in opf_root.findall(".//opf:metadata/opf:meta", _OPF_NS):
            if meta.attrib.get("name") == "cover" and meta.attrib.get("content"):
                cover_id = meta.attrib["content"]

        for item in opf_root.findall(".//opf:manifest/opf:item", _OPF_NS):
            item_id = item.attrib.get("id", "")
            href = item.attrib.get("href", "")
            media_type = item.attrib.get("media-type", "")
            props = item.attrib.get("properties", "")
            if not item_id or not href:
                continue
            zip_href = _norm_zip_path(opf_dir, href)
            manifest[item_id] = (zip_href, media_type, props)
            if "cover-image" in props.split():
                cover_id = item_id
            if "nav" in props.split():
                nav_href = zip_href
            if media_type == "application/x-dtbncx+xml":
                ncx_href = zip_href

        spine_el = opf_root.find(".//opf:spine", _OPF_NS)
        if spine_el is not None and not ncx_href:
            toc_id = spine_el.attrib.get("toc")
            if toc_id and toc_id in manifest:
                ncx_href = manifest[toc_id][0]

        toc_map: dict[str, str] = {}
        if nav_href:
            toc_map.update(_parse_nav_toc(zf, nav_href))
        if ncx_href and not toc_map:
            toc_map.update(_parse_ncx_toc(zf, ncx_href))

        spine_parts: list[_SpinePart] = []
        if spine_el is not None:
            for itemref in spine_el.findall("./opf:itemref", _OPF_NS):
                idref = itemref.attrib.get("idref", "")
                if idref not in manifest:
                    continue
                zip_href, media_type, _ = manifest[idref]
                if media_type not in ("application/xhtml+xml", "text/html", ""):
                    continue
                try:
                    raw_bytes = zf.read(zip_href)
                except KeyError:
                    continue
                body_html, heading_text, first_img = _rewrite_body_html(
                    raw_bytes, zip_href, book_id_placeholder
                )
                spine_parts.append(
                    _SpinePart(
                        href=zip_href,
                        body_html=body_html,
                        heading_text=heading_text,
                        toc_label=toc_map.get(zip_href),
                        first_image_href=first_img,
                    )
                )

        cover_href: str | None = None
        if cover_id and cover_id in manifest:
            cover_href = manifest[cover_id][0]
        elif "cover" in manifest:
            cover_href = manifest["cover"][0]
        else:
            for part in spine_parts:
                if part.first_image_href:
                    cover_href = part.first_image_href
                    break

        chapters = _group_spine_into_chapters(spine_parts, toc_map)

    return ParsedBook(
        file_path=str(epub_path.resolve()),
        file_hash=file_hash,
        title=title or epub_path.stem,
        author=author or "不明",
        cover_href=cover_href,
        chapters=chapters,
    )


def read_epub_asset(epub_path: Path, asset_path: str) -> tuple[bytes, str] | None:
    """Safely read an embedded image/asset from an EPUB zip archive."""
    clean = posixpath.normpath(asset_path.lstrip("/"))
    if not clean or clean.startswith("..") or clean == ".":
        return None

    with zipfile.ZipFile(epub_path, "r") as zf:
        names = set(zf.namelist())
        if clean not in names:
            return None
        data = zf.read(clean)

    ext = posixpath.splitext(clean)[1].lower()
    mime = _MIME_OVERRIDES.get(ext) or mimetypes.guess_type(clean)[0] or "application/octet-stream"
    return data, mime
