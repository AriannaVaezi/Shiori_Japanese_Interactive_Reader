"""Ruby-aware SudachiPy Japanese tokenizer and sentence segmenter."""

from __future__ import annotations

import html
import random
import re
import threading
from dataclasses import dataclass

import sudachipy
from bs4 import BeautifulSoup, NavigableString, Tag

_TOKENIZER_LOCK = threading.Lock()
_DICTIONARY: sudachipy.Dictionary | None = None
_TOKENIZER: sudachipy.Tokenizer | None = None

_SKIP_POS = {"補助記号", "空白"}
_OPENING_PUNCT = set("「『（([【〈《“‘")
_BUNSETSU_ATTACH_POS = {"助詞", "助動詞", "接尾辞", "補助記号"}
_TARGET_PARTICLES = {"は", "が", "を", "に", "で", "へ", "と", "も", "から", "まで", "より", "の"}
_PARTICLE_GROUPS = [
    ["は", "が", "も", "を"],
    ["に", "で", "へ", "を"],
    ["から", "まで", "より", "と"],
    ["の", "に", "と", "で"],
]
_CONTENT_POS = {"名詞", "動詞", "形容詞", "形状詞", "副詞"}
_EXCLUDE_SUBPOS = {"数詞", "代名詞", "非自立可能"}
_KANJI_RE = re.compile(r"[\u4e00-\u9fff]")
_SENTENCE_SPLIT_RE = re.compile(r"[^。！？!?]+(?:[。！？!?]+[」』）)”’。！？!?]*|$)")
_LEADING_WS_RE = re.compile(r"^[\s\u3000]+")

_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "li"}


@dataclass
class RubySpan:
    start: int
    end: int
    reading: str


def _get_tokenizer() -> sudachipy.Tokenizer:
    global _DICTIONARY, _TOKENIZER
    if _TOKENIZER is None:
        with _TOKENIZER_LOCK:
            if _TOKENIZER is None:
                _DICTIONARY = sudachipy.Dictionary()
                _TOKENIZER = _DICTIONARY.tokenizer(sudachipy.Tokenizer.SplitMode.C)
    return _TOKENIZER


def katakana_to_hiragana(text: str) -> str:
    """Convert full-width katakana characters in text to hiragana."""
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


def _lemma_reading_hiragana(tokenizer: sudachipy.Tokenizer, lemma: str, fallback: str) -> str:
    """Compute the dictionary reading of a lemma in hiragana."""
    if not lemma:
        return katakana_to_hiragana(fallback)
    morphemes = tokenizer.tokenize(lemma)
    if not morphemes:
        return katakana_to_hiragana(fallback)
    if len(morphemes) == 1:
        r = morphemes[0].reading_form()
        return katakana_to_hiragana(r if r and r != "*" else lemma)
    joined = "".join(m.reading_form() if m.reading_form() != "*" else m.surface() for m in morphemes)
    return katakana_to_hiragana(joined)


def text_to_hiragana_reading(text: str) -> str:
    """Return a hiragana reading for an arbitrary selected Japanese span."""
    clean = (text or "").strip()
    if not clean:
        return ""
    return _lemma_reading_hiragana(_get_tokenizer(), clean, clean)


def lemmatize_single_word(text: str) -> str | None:
    """Return the dictionary lemma if `text` is a single inflected word."""
    clean = (text or "").strip()
    if not clean:
        return None
    morphemes = list(_get_tokenizer().tokenize(clean))
    if not morphemes:
        return None
    if len(morphemes) == 1:
        dict_form = morphemes[0].dictionary_form()
        return dict_form if dict_form and dict_form != clean else None
    if morphemes[0].part_of_speech()[0] in {"動詞", "形容詞"} and all(
        m.part_of_speech()[0] in {"助動詞", "助詞"} for m in morphemes[1:]
    ):
        dict_form = morphemes[0].dictionary_form()
        return dict_form if dict_form and dict_form != clean else None
    return None


def find_exact_word_span(sentence: str, lemma: str) -> tuple[int, int, str] | None:
    """Locate an exact standalone whole-token occurrence of `lemma` inside `sentence`."""
    clean = (sentence or "").strip()
    target = (lemma or "").strip()
    if not clean or not target:
        return None

    morphemes = list(_get_tokenizer().tokenize(clean))
    for idx, m in enumerate(morphemes):
        surf = m.surface()
        pos0 = m.part_of_speech()[0]
        if surf != target or m.dictionary_form() != target or pos0 not in _CONTENT_POS:
            continue
        if clean[m.begin() : m.end()] != target:
            continue
        if pos0 in {"動詞", "形容詞"} and idx + 1 < len(morphemes):
            if morphemes[idx + 1].part_of_speech()[0] == "助動詞":
                continue
        return m.begin(), m.end(), surf
    return None


def tokenize_plain_text(text: str) -> list[dict]:
    """Tokenize plain Japanese text (e.g. manga OCR bubbles) into clickable SudachiPy token dicts."""
    raw = text or ""
    if not raw:
        return []

    tokenizer = _get_tokenizer()
    morphemes = list(tokenizer.tokenize(raw))
    tokens: list[dict] = []
    cursor = 0

    for m in morphemes:
        m_start = m.begin()
        m_end = m.end()
        if m_start > cursor:
            gap = raw[cursor:m_start]
            tokens.append(
                {
                    "surface": gap,
                    "lemma": "",
                    "norm": "",
                    "reading": "",
                    "pos": "空白",
                    "clickable": False,
                }
            )
        surface = m.surface()
        pos0 = m.part_of_speech()[0]
        is_clickable = pos0 not in _SKIP_POS and bool(surface.strip())
        lemma = m.dictionary_form()
        norm = m.normalized_form()
        reading = (
            _lemma_reading_hiragana(tokenizer, lemma, m.reading_form())
            if is_clickable
            else ""
        )
        tokens.append(
            {
                "surface": surface,
                "lemma": lemma,
                "norm": norm,
                "reading": reading,
                "pos": pos0,
                "clickable": is_clickable,
            }
        )
        cursor = m_end

    if cursor < len(raw):
        tail = raw[cursor:]
        tokens.append(
            {
                "surface": tail,
                "lemma": "",
                "norm": "",
                "reading": "",
                "pos": "空白",
                "clickable": False,
            }
        )
    return tokens


def _extract_ruby_element(
    ruby_tag: Tag,
    chars: list[str],
    ruby_spans: list[RubySpan],
) -> None:
    """Extract plain base text and <rb>/<rt> alignment spans from a <ruby> tag."""
    has_rb = ruby_tag.find("rb", recursive=False) is not None
    if has_rb:
        children = [c for c in ruby_tag.children if isinstance(c, Tag) or (isinstance(c, NavigableString) and str(c))]
        i = 0
        while i < len(children):
            node = children[i]
            if isinstance(node, Tag) and node.name == "rb":
                base_text = node.get_text("", strip=False)
                rt_text = ""
                j = i + 1
                while j < len(children):
                    nxt = children[j]
                    if isinstance(nxt, Tag) and nxt.name in ("rp",):
                        j += 1
                        continue
                    if isinstance(nxt, Tag) and nxt.name == "rt":
                        rt_text = nxt.get_text("", strip=False)
                        j += 1
                        break
                    break
                start = len(chars)
                chars.extend(base_text)
                end = len(chars)
                if end > start and rt_text:
                    ruby_spans.append(RubySpan(start=start, end=end, reading=rt_text))
                i = j
            elif isinstance(node, Tag) and node.name in ("rt", "rp"):
                i += 1
            elif isinstance(node, Tag):
                start = len(chars)
                chars.extend(node.get_text("", strip=False))
                i += 1
            else:
                chars.extend(str(node))
                i += 1
        return

    # HTML5 <ruby>ベース<rt>ふりがな</rt></ruby> without <rb>
    pending_start: int | None = None
    for child in ruby_tag.children:
        if isinstance(child, Tag) and child.name == "rp":
            continue
        if isinstance(child, Tag) and child.name == "rt":
            rt_text = child.get_text("", strip=False)
            if pending_start is not None and len(chars) > pending_start and rt_text:
                ruby_spans.append(RubySpan(start=pending_start, end=len(chars), reading=rt_text))
            pending_start = None
        else:
            text = child.get_text("", strip=False) if isinstance(child, Tag) else str(child)
            if text:
                if pending_start is None:
                    pending_start = len(chars)
                chars.extend(text)


def _walk_inline(
    node: Tag | NavigableString,
    chars: list[str],
    ruby_spans: list[RubySpan],
    inline_breaks: dict[int, list[str]],
) -> None:
    """Flatten an inline HTML subtree into plain characters plus ruby/break markers."""
    if isinstance(node, NavigableString):
        text = str(node)
        if text:
            chars.extend(text)
        return
    if not isinstance(node, Tag):
        return
    if node.name in ("rt", "rp"):
        return
    if node.name == "ruby":
        _extract_ruby_element(node, chars, ruby_spans)
        return
    if node.name == "br":
        inline_breaks.setdefault(len(chars), []).append("<br/>")
        return
    if node.name == "img":
        inline_breaks.setdefault(len(chars), []).append(str(node))
        return
    for child in node.children:
        _walk_inline(child, chars, ruby_spans, inline_breaks)


def _render_slice_with_ruby(
    plain: str,
    sl_start: int,
    sl_end: int,
    ruby_spans: list[RubySpan],
    inline_breaks: dict[int, list[str]],
) -> str:
    """Render plain[sl_start:sl_end] back into HTML preserving <ruby> and <br/>."""
    parts: list[str] = []
    pos = sl_start
    span_idx = 0
    while span_idx < len(ruby_spans) and ruby_spans[span_idx].end <= sl_start:
        span_idx += 1

    def emit_plain(a: int, b: int) -> None:
        if a >= b:
            return
        buf: list[str] = []
        for idx in range(a, b):
            if idx in inline_breaks:
                if buf:
                    parts.append(html.escape("".join(buf), quote=False))
                    buf.clear()
                parts.extend(inline_breaks[idx])
            buf.append(plain[idx])
        if buf:
            parts.append(html.escape("".join(buf), quote=False))

    in_ruby = False
    while pos < sl_end:
        if span_idx < len(ruby_spans) and ruby_spans[span_idx].start < sl_end:
            sp = ruby_spans[span_idx]
            if pos < sp.start:
                if in_ruby:
                    parts.append("</ruby>")
                    in_ruby = False
                emit_plain(pos, sp.start)
                pos = sp.start
                continue
            # Emit ruby segment (clamped defensively, though atomic merging prevents splits)
            seg_start = max(pos, sp.start)
            seg_end = min(sl_end, sp.end)
            if not in_ruby:
                parts.append("<ruby>")
                in_ruby = True
            base_esc = html.escape(plain[seg_start:seg_end], quote=False)
            rt_esc = html.escape(sp.reading, quote=False)
            parts.append(f"<rb>{base_esc}</rb><rt>{rt_esc}</rt>")
            pos = seg_end
            span_idx += 1
            # Close ruby if next span is not adjacent
            if not (span_idx < len(ruby_spans) and ruby_spans[span_idx].start == pos and ruby_spans[span_idx].start < sl_end):
                parts.append("</ruby>")
                in_ruby = False
        else:
            if in_ruby:
                parts.append("</ruby>")
                in_ruby = False
            emit_plain(pos, sl_end)
            pos = sl_end

    if in_ruby:
        parts.append("</ruby>")
    return "".join(parts)


def _merge_cross_ruby_morphemes(
    morphemes: list,
    sent_offset: int,
    ruby_spans: list[RubySpan],
) -> list[dict]:
    """Merge adjacent morphemes if any split occurs inside a single atomic <rb> span."""
    if not morphemes:
        return []

    def span_containing_interior(abs_pos: int) -> RubySpan | None:
        for sp in ruby_spans:
            if sp.start < abs_pos < sp.end:
                return sp
        return None

    groups: list[list] = [[morphemes[0]]]
    for m in morphemes[1:]:
        boundary = sent_offset + m.begin()
        if span_containing_interior(boundary) is not None:
            groups[-1].append(m)
        else:
            groups.append([m])

    merged_tokens: list[dict] = []
    for grp in groups:
        first = grp[0]
        last = grp[-1]
        merged_tokens.append(
            {
                "begin": sent_offset + first.begin(),
                "end": sent_offset + last.end(),
                "surface": "".join(x.surface() for x in grp),
                "morphemes": grp,
            }
        )
    return merged_tokens


def _tokenize_sentence(
    plain: str,
    sent_start: int,
    sent_end: int,
    sent_id: str,
    ruby_spans: list[RubySpan],
    inline_breaks: dict[int, list[str]],
    tokenizer: sudachipy.Tokenizer,
) -> tuple[str, str]:
    """Tokenize one sentence slice and return (sentence_html, plain_sentence_text)."""
    sent_text = plain[sent_start:sent_end]
    morphemes = list(tokenizer.tokenize(sent_text))
    merged = _merge_cross_ruby_morphemes(morphemes, sent_start, ruby_spans)

    tok_parts: list[str] = []
    cursor = sent_start
    for tok in merged:
        t_start = tok["begin"]
        t_end = tok["end"]
        if t_start > cursor:
            tok_parts.append(
                _render_slice_with_ruby(plain, cursor, t_start, ruby_spans, inline_breaks)
            )
        inner_html = _render_slice_with_ruby(plain, t_start, t_end, ruby_spans, inline_breaks)
        surface = tok["surface"]
        ms = tok["morphemes"]

        if len(ms) == 1:
            m = ms[0]
            pos0 = m.part_of_speech()[0]
            is_clickable = pos0 not in _SKIP_POS and bool(surface.strip())
            lemma = m.dictionary_form()
            norm = m.normalized_form()
            reading = _lemma_reading_hiragana(tokenizer, lemma, m.reading_form())
        else:
            # Compound merged to keep a multi-kanji jukujikun ruby span intact
            pos0 = ms[0].part_of_speech()[0]
            is_clickable = any(m.part_of_speech()[0] not in _SKIP_POS for m in ms) and bool(surface.strip())
            lemma = surface
            norm = surface
            # Prefer ruby reading if a single ruby span covers the merged token
            matching_ruby = next(
                (sp.reading for sp in ruby_spans if sp.start == t_start and sp.end == t_end),
                None,
            )
            if matching_ruby:
                reading = katakana_to_hiragana(matching_ruby)
            else:
                reading = _lemma_reading_hiragana(tokenizer, surface, surface)

        if is_clickable:
            tok_parts.append(
                f'<span class="tok" '
                f'data-lemma="{html.escape(lemma, quote=True)}" '
                f'data-norm="{html.escape(norm, quote=True)}" '
                f'data-surface="{html.escape(surface, quote=True)}" '
                f'data-reading="{html.escape(reading, quote=True)}" '
                f'data-pos="{html.escape(pos0, quote=True)}">{inner_html}</span>'
            )
        else:
            tok_parts.append(inner_html)
        cursor = t_end

    if cursor < sent_end:
        tok_parts.append(
            _render_slice_with_ruby(plain, cursor, sent_end, ruby_spans, inline_breaks)
        )

    sent_html = (
        f'<span class="sent" data-sent-id="{html.escape(sent_id, quote=True)}">'
        f'{"".join(tok_parts)}'
        f'<button type="button" class="sent-ask-btn" data-sent-id="{html.escape(sent_id, quote=True)}" '
        f'title="Ask about grammar" aria-label="Ask about grammar">?</button>'
        f"</span>"
    )
    return sent_html, sent_text


def _is_leaf_block(tag: Tag) -> bool:
    if tag.name in _BLOCK_TAGS:
        return True
    if tag.name == "div":
        has_block_child = tag.find(list(_BLOCK_TAGS) + ["figure"]) is not None
        has_text = bool(tag.get_text("", strip=True))
        return has_text and not has_block_child
    return False


def tokenize_chapter_html(raw_html: str, chapter_index: int) -> tuple[str, list[dict], int]:
    """Tokenize chapter HTML into clickable word spans while preserving <ruby> furigana."""
    if not raw_html.strip():
        return "", [], 0

    soup = BeautifulSoup(raw_html, "lxml")
    body = soup.body or soup
    tokenizer = _get_tokenizer()

    sentences: list[dict] = []
    sent_counter = 0
    total_chars = 0

    candidate_blocks = [tag for tag in body.find_all(_BLOCK_TAGS | {"div"}) if _is_leaf_block(tag)]

    for block in candidate_blocks:
        # Leave blocks containing non-text media (like <img>) untouched if they have no text
        if block.find("img") is not None and not block.get_text("", strip=True):
            continue

        chars: list[str] = []
        ruby_spans: list[RubySpan] = []
        inline_breaks: dict[int, list[str]] = {}

        for child in block.children:
            _walk_inline(child, chars, ruby_spans, inline_breaks)

        plain = "".join(chars)
        if not plain.strip():
            continue

        total_chars += len(plain.strip())
        out_fragments: list[str] = []
        cursor = 0

        for match in _SENTENCE_SPLIT_RE.finditer(plain):
            m_start, m_end = match.start(), match.end()
            if m_start > cursor:
                out_fragments.append(
                    _render_slice_with_ruby(plain, cursor, m_start, ruby_spans, inline_breaks)
                )

            raw_slice = plain[m_start:m_end]
            ws_match = _LEADING_WS_RE.match(raw_slice)
            ws_len = len(ws_match.group(0)) if ws_match else 0
            if ws_len:
                out_fragments.append(
                    _render_slice_with_ruby(plain, m_start, m_start + ws_len, ruby_spans, inline_breaks)
                )

            s_start = m_start + ws_len
            s_end = m_end
            if s_start < s_end and plain[s_start:s_end].strip():
                sent_id = f"c{chapter_index}_s{sent_counter}"
                sent_counter += 1
                sent_html, sent_text = _tokenize_sentence(
                    plain,
                    s_start,
                    s_end,
                    sent_id,
                    ruby_spans,
                    inline_breaks,
                    tokenizer,
                )
                out_fragments.append(sent_html)
                sentences.append({"sent_id": sent_id, "text": sent_text})
            cursor = m_end

        if cursor < len(plain):
            out_fragments.append(
                _render_slice_with_ruby(plain, cursor, len(plain), ruby_spans, inline_breaks)
            )

        # Replace block's contents with tokenized fragment
        block.clear()
        frag = BeautifulSoup(f"<div>{''.join(out_fragments)}</div>", "lxml").div
        if frag is not None:
            for node in list(frag.contents):
                block.append(node.extract())

    tokenized_html = "".join(str(child) for child in body.contents)
    return tokenized_html, sentences, total_chars


def chunk_into_bunsetsu(sentence: str) -> list[str]:
    """Split a Japanese sentence into grammatical bunsetsu chunks (capped at 6)."""
    clean = sentence.strip()
    if not clean:
        return []

    tokenizer = _get_tokenizer()
    morphemes = list(tokenizer.tokenize(clean))
    chunks: list[str] = []
    pending_prefix = ""

    for m in morphemes:
        surf = m.surface()
        pos = m.part_of_speech()
        pos0 = pos[0]
        pos1 = pos[1] if len(pos) > 1 else "*"

        if pos0 == "空白" or not surf.strip():
            continue

        if pos0 == "接頭辞" or (pos0 == "補助記号" and surf in _OPENING_PUNCT):
            pending_prefix += surf
            continue

        is_attached = pos0 in _BUNSETSU_ATTACH_POS or (pos1 == "非自立可能" and bool(chunks))
        if is_attached and chunks:
            chunks[-1] = chunks[-1] + pending_prefix + surf
            pending_prefix = ""
        else:
            chunks.append(pending_prefix + surf)
            pending_prefix = ""

    if pending_prefix:
        if chunks:
            chunks[-1] += pending_prefix
        else:
            chunks.append(pending_prefix)

    chunks = [c.strip() for c in chunks if c.strip()]
    while len(chunks) > 6:
        best_idx = min(
            range(len(chunks) - 1),
            key=lambda i: len(chunks[i]) + len(chunks[i + 1]),
        )
        merged = chunks[best_idx] + chunks[best_idx + 1]
        chunks = chunks[:best_idx] + [merged] + chunks[best_idx + 2 :]

    return chunks


def extract_particle_quiz(sentence: str) -> dict | None:
    """Create a 4-choice particle fill-in question from a Japanese sentence."""
    clean = sentence.strip()
    if len(clean) < 5:
        return None

    tokenizer = _get_tokenizer()
    morphemes = list(tokenizer.tokenize(clean))
    tail_stripped_len = len(clean.rstrip("。！？!?」』）)”’"))

    candidates = []
    for idx, m in enumerate(morphemes):
        pos = m.part_of_speech()
        surf = m.surface()
        if pos[0] != "助詞" or surf not in _TARGET_PARTICLES:
            continue
        if pos[1] not in {"格助詞", "係助詞", "副助詞"}:
            continue
        if (idx > 0 and morphemes[idx - 1].part_of_speech()[0] == "助詞") or (
            idx + 1 < len(morphemes) and morphemes[idx + 1].part_of_speech()[0] == "助詞"
        ):
            continue
        if surf == "の" and m.end() >= tail_stripped_len:
            continue
        candidates.append(m)

    if not candidates:
        return None

    center = len(clean) / 2.0
    chosen = min(candidates, key=lambda m: abs(((m.begin() + m.end()) / 2.0) - center))
    particle = chosen.surface()
    blanked = f"{clean[:chosen.begin()]}＿＿{clean[chosen.end():]}"
    if blanked.replace("＿＿", particle, 1) != clean:
        return None

    group = next((g for g in _PARTICLE_GROUPS if particle in g), ["は", "が", "を", "に"])
    choices = list(dict.fromkeys([particle] + [p for p in group if p != particle]))
    for extra in ("は", "が", "を", "に", "で", "も", "と", "から"):
        if len(choices) >= 4:
            break
        if extra not in choices:
            choices.append(extra)
    choices = choices[:4]
    rng = random.Random(clean + particle)
    rng.shuffle(choices)

    return {
        "blanked_sentence": blanked,
        "correct": particle,
        "choices": choices,
        "full_sentence": clean,
    }


def extract_chapter_vocab_pool(sentences: list[dict], max_items: int = 80) -> list[dict]:
    """Extract unique content words from a chapter's sentences for quiz distractors/fallbacks."""
    tokenizer = _get_tokenizer()
    pool: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for sent in sentences:
        sent_id = sent.get("sent_id", "")
        sent_text = sent.get("text", "")
        if not sent_text:
            continue
        for m in tokenizer.tokenize(sent_text):
            pos = m.part_of_speech()
            pos0 = pos[0]
            pos1 = pos[1] if len(pos) > 1 else "*"
            if pos0 not in _CONTENT_POS or pos1 in _EXCLUDE_SUBPOS:
                continue
            surface = m.surface()
            if len(surface) < 2 and not _KANJI_RE.search(surface):
                continue
            lemma = m.dictionary_form()
            reading = _lemma_reading_hiragana(tokenizer, lemma, m.reading_form())
            key = (lemma, reading)
            if key in seen:
                continue
            seen.add(key)
            pool.append(
                {
                    "lemma": lemma,
                    "surface": surface,
                    "reading": reading,
                    "pos": pos0,
                    "sent_id": sent_id,
                    "sentence_text": sent_text,
                }
            )
            if len(pool) >= max_items:
                return pool
    return pool
