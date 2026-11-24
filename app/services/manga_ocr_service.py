"""CPU-pinned Japanese manga OCR service wrapping mokuro / manga-ocr with SudachiPy tokenization."""

from __future__ import annotations

import importlib.util
import io
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from PIL import Image

from app.tokenizer import tokenize_plain_text

_OCR_LOCK = threading.Lock()
_MPOCR: Any = None


def is_ocr_available() -> bool:
    """Return True if the optional mokuro + manga-ocr packages are installed."""
    return (
        importlib.util.find_spec("mokuro") is not None
        and importlib.util.find_spec("manga_ocr") is not None
    )


def _get_mpocr() -> Any:
    """Lazily initialize MangaPageOcr on CPU so GPU VRAM remains free for llama-server."""
    global _MPOCR
    if _MPOCR is not None:
        return _MPOCR

    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise RuntimeError("MangaPageOcr initialization is disabled under pytest.")

    with _OCR_LOCK:
        if _MPOCR is None:
            from mokuro.manga_page_ocr import MangaPageOcr

            _MPOCR = MangaPageOcr(force_cpu=True)
    return _MPOCR


def _normalize_block(
    idx: int,
    page_index: int,
    raw_box: list[Any] | tuple[Any, ...],
    vertical: bool,
    lines: list[Any],
    img_w: int,
    img_h: int,
) -> dict[str, Any] | None:
    """Convert raw detector coordinates and text lines into normalized percentage & token data."""
    if img_w <= 0 or img_h <= 0 or len(raw_box) < 4:
        return None

    x1 = max(0, min(img_w, int(round(float(raw_box[0])))))
    y1 = max(0, min(img_h, int(round(float(raw_box[1])))))
    x2 = max(x1, min(img_w, int(round(float(raw_box[2])))))
    y2 = max(y1, min(img_h, int(round(float(raw_box[3])))))
    if x2 <= x1 or y2 <= y1:
        return None

    lines_clean = [str(ln).strip() for ln in (lines or []) if str(ln).strip()]
    text = "".join(lines_clean).strip()
    if not text:
        return None

    box_pct = [
        round(x1 / img_w * 100.0, 3),
        round(y1 / img_h * 100.0, 3),
        round((x2 - x1) / img_w * 100.0, 3),
        round((y2 - y1) / img_h * 100.0, 3),
    ]
    return {
        "block_id": f"p{page_index}_b{idx}",
        "box": [x1, y1, x2, y2],
        "box_pct": box_pct,
        "vertical": bool(vertical),
        "lines": lines_clean,
        "text": text,
        "tokens": tokenize_plain_text(text),
    }


def ensure_blocks_tokenized(ocr_data: dict[str, Any]) -> dict[str, Any]:
    """Ensure cached OCR blocks include up-to-date SudachiPy token lists."""
    blocks = ocr_data.get("blocks") or []
    for blk in blocks:
        text = (blk.get("text") or "").strip()
        if text and "tokens" not in blk:
            blk["tokens"] = tokenize_plain_text(text)
    return ocr_data


def normalize_sidecar_page(
    raw_page: dict[str, Any],
    page_index: int,
    fallback_w: int = 0,
    fallback_h: int = 0,
) -> dict[str, Any]:
    """Normalize a sibling .mokuro page JSON entry into Shiori's OCR schema."""
    img_w = int(raw_page.get("img_width") or fallback_w or 0)
    img_h = int(raw_page.get("img_height") or fallback_h or 0)
    blocks: list[dict[str, Any]] = []
    for raw_blk in raw_page.get("blocks") or []:
        if not isinstance(raw_blk, dict):
            continue
        norm = _normalize_block(
            idx=len(blocks),
            page_index=page_index,
            raw_box=raw_blk.get("box") or [],
            vertical=bool(raw_blk.get("vertical", True)),
            lines=raw_blk.get("lines") or [],
            img_w=img_w,
            img_h=img_h,
        )
        if norm is not None:
            blocks.append(norm)
    return {
        "ocr_available": True,
        "img_width": img_w,
        "img_height": img_h,
        "blocks": blocks,
        "engine": "mokuro-sidecar",
    }


def _save_temp_png(image: Image.Image) -> Path:
    fd, tmp_name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    tmp_path = Path(tmp_name)
    image.save(tmp_path, format="PNG")
    return tmp_path


def run_page_ocr(image_bytes: bytes, page_index: int) -> dict[str, Any]:
    """Detect all text boxes on a manga page, run CPU OCR, and tokenize each block."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        rgb = im.convert("RGB")
        img_w, img_h = rgb.size

    if not is_ocr_available():
        return {
            "ocr_available": False,
            "error": "Install optional manga OCR extras: uv sync --extra manga-ocr",
            "img_width": img_w,
            "img_height": img_h,
            "blocks": [],
        }

    tmp_path: Path | None = None
    try:
        mpocr = _get_mpocr()
        tmp_path = _save_temp_png(rgb)
        with _OCR_LOCK:
            raw_res = mpocr(tmp_path)
    except Exception as exc:
        return {
            "ocr_available": False,
            "error": f"Manga OCR unavailable: {exc}",
            "img_width": img_w,
            "img_height": img_h,
            "blocks": [],
        }
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    blocks: list[dict[str, Any]] = []
    for raw_blk in raw_res.get("blocks") or []:
        norm = _normalize_block(
            idx=len(blocks),
            page_index=page_index,
            raw_box=raw_blk.get("box") or [],
            vertical=bool(raw_blk.get("vertical", True)),
            lines=raw_blk.get("lines") or [],
            img_w=int(raw_res.get("img_width") or img_w),
            img_h=int(raw_res.get("img_height") or img_h),
        )
        if norm is not None:
            blocks.append(norm)

    return {
        "ocr_available": True,
        "img_width": img_w,
        "img_height": img_h,
        "blocks": blocks,
        "engine": "mokuro",
    }


def run_crop_ocr(
    image_bytes: bytes,
    page_index: int,
    x: float,
    y: float,
    w: float,
    h: float,
) -> dict[str, Any]:
    """Run CPU OCR on a user-selected normalized [0..1] crop region of a manga page."""
    with Image.open(io.BytesIO(image_bytes)) as im:
        rgb = im.convert("RGB")
        img_w, img_h = rgb.size

    x_clamped = max(0.0, min(1.0, float(x)))
    y_clamped = max(0.0, min(1.0, float(y)))
    w_clamped = max(0.0, min(1.0 - x_clamped, float(w)))
    h_clamped = max(0.0, min(1.0 - y_clamped, float(h)))

    box_pct = [
        round(x_clamped * 100.0, 3),
        round(y_clamped * 100.0, 3),
        round(w_clamped * 100.0, 3),
        round(h_clamped * 100.0, 3),
    ]

    if not is_ocr_available():
        return {
            "ocr_available": False,
            "error": "Install optional manga OCR extras: uv sync --extra manga-ocr",
            "block_id": f"p{page_index}_crop",
            "text": "",
            "tokens": [],
            "box_pct": box_pct,
        }

    pad_px = 4
    left = max(0, int(round(x_clamped * img_w)) - pad_px)
    top = max(0, int(round(y_clamped * img_h)) - pad_px)
    right = min(img_w, max(left + 8, int(round((x_clamped + w_clamped) * img_w)) + pad_px))
    bottom = min(img_h, max(top + 8, int(round((y_clamped + h_clamped) * img_h)) + pad_px))
    crop_img = rgb.crop((left, top, right, bottom))

    text = ""
    tmp_path: Path | None = None
    try:
        mpocr = _get_mpocr()
        # Use detector + multi-line crop splitting first on medium/large selections (handles vertical bubbles)
        if crop_img.width >= 48 and crop_img.height >= 48:
            tmp_path = _save_temp_png(crop_img)
            with _OCR_LOCK:
                raw_res = mpocr(tmp_path)
            raw_blocks = raw_res.get("blocks") or []
            if raw_blocks:
                parts = []
                for blk in raw_blocks:
                    blk_txt = "".join(str(ln).strip() for ln in (blk.get("lines") or []))
                    if blk_txt:
                        parts.append(blk_txt)
                text = "".join(parts).strip()

        if not text:
            # Fallback for single-line / SFX crops: invoke MangaOcr directly
            direct_img = crop_img
            with _OCR_LOCK:
                text = str(mpocr.mocr(direct_img) or "").strip()
    except Exception as exc:
        return {
            "ocr_available": False,
            "error": f"Manga crop OCR failed: {exc}",
            "block_id": f"p{page_index}_crop",
            "text": "",
            "tokens": [],
            "box_pct": box_pct,
        }
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)

    return {
        "ocr_available": True,
        "block_id": f"p{page_index}_crop",
        "text": text,
        "tokens": tokenize_plain_text(text),
        "box_pct": box_pct,
    }
