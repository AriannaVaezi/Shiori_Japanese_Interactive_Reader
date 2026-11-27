/** Shared client state and safe DOM helpers. */

export const state = {
  settings: {
    llm_provider: "llamacpp",
    llamacpp_url: "http://127.0.0.1:8080",
    llamacpp_model: "",
    llamacpp_model_path: "",
    llamacpp_autostart: "true",
    has_llamacpp_key: false,
    llamacpp_api_key_masked: "",
    ollama_url: "http://localhost:11434",
    ollama_model: "",
    gemini_model: "gemini-2.5-flash",
    has_gemini_key: false,
    gemini_api_key_masked: "",
    reading_mode: "horizontal",
    furigana_mode: "all",
    theme: "paper",
    font_size: "20",
    quiz_timer_minutes: "15",
    manga_layout: "spread",
    manga_spread_offset: "1",
    manga_ocr_overlay: "hover",
    hide_annotations: "false",
  },
  books: [],
  currentBook: null,
  currentChapter: null,
  grammarThreads: [],
  troubleItems: [],
  notes: [],
  sessionStartedAt: new Date().toISOString(),
  activeThreadKey: null,
  lastActivityMs: Date.now(),
};

export function extractCleanText(rootNode) {
  if (!rootNode) return "";
  const clone = rootNode.cloneNode(true);
  clone.querySelectorAll?.("rt, rp, .sent-ask-btn").forEach((el) => el.remove());
  return (clone.textContent || "").trim();
}

export function extractRangeCleanText(range) {
  if (!range) return "";
  const frag = range.cloneContents();
  frag.querySelectorAll?.("rt, rp, .sent-ask-btn").forEach((el) => el.remove());
  return (frag.textContent || "").trim();
}

/** Render simple safe markdown (bold, inline code, bullet lists, paragraphs) into DOM nodes. */
export function renderSafeMarkdown(container, markdownText) {
  container.replaceChildren();
  const lines = String(markdownText || "").replace(/\r\n/g, "\n").split("\n");
  let currentList = null;
  let paragraphBuf = [];

  const appendFormattedInline = (parent, text) => {
    const pattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
    let lastIndex = 0;
    let match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parent.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
      }
      const token = match[0];
      if (token.startsWith("**") && token.endsWith("**")) {
        const strong = document.createElement("strong");
        strong.textContent = token.slice(2, -2);
        parent.appendChild(strong);
      } else if (token.startsWith("`") && token.endsWith("`")) {
        const code = document.createElement("code");
        code.textContent = token.slice(1, -1);
        parent.appendChild(code);
      }
      lastIndex = pattern.lastIndex;
    }
    if (lastIndex < text.length) {
      parent.appendChild(document.createTextNode(text.slice(lastIndex)));
    }
  };

  const flushParagraph = () => {
    if (!paragraphBuf.length) return;
    const p = document.createElement("p");
    appendFormattedInline(p, paragraphBuf.join(" "));
    container.appendChild(p);
    paragraphBuf = [];
  };

  const flushList = () => {
    if (!currentList) return;
    container.appendChild(currentList);
    currentList = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushParagraph();
      flushList();
      continue;
    }
    const bulletMatch = line.match(/^[-*•]\s+(.*)$/) || line.match(/^\d+\.\s+(.*)$/);
    if (bulletMatch) {
      flushParagraph();
      if (!currentList) {
        currentList = document.createElement("ul");
      }
      const li = document.createElement("li");
      appendFormattedInline(li, bulletMatch[1]);
      currentList.appendChild(li);
      continue;
    }
    if (line.startsWith("#")) {
      flushParagraph();
      flushList();
      const p = document.createElement("p");
      const strong = document.createElement("strong");
      strong.textContent = line.replace(/^#+\s*/, "");
      p.appendChild(strong);
      container.appendChild(p);
      continue;
    }
    flushList();
    paragraphBuf.push(line);
  }
  flushParagraph();
  flushList();
}

export const HIGHLIGHT_COLORS = ["yellow", "blue", "pink", "orange", "green", "purple"];

const HL_MARKUP_RE = /==(?:\[(yellow|blue|pink|orange|green|purple)\])?([\s\S]+?)==/g;

export function parseHighlightSegments(rawText) {
  const src = String(rawText || "");
  if (!src) return [];
  const segments = [];
  let lastIndex = 0;
  HL_MARKUP_RE.lastIndex = 0;
  let match;
  while ((match = HL_MARKUP_RE.exec(src)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: src.slice(lastIndex, match.index), color: null });
    }
    if (match[2]) {
      segments.push({ text: match[2], color: match[1] || "yellow" });
    }
    lastIndex = HL_MARKUP_RE.lastIndex;
  }
  if (lastIndex < src.length) {
    segments.push({ text: src.slice(lastIndex), color: null });
  }
  return segments;
}

export function stripHighlightMarkup(rawText) {
  return parseHighlightSegments(rawText)
    .map((seg) => seg.text)
    .join("");
}

export function rawOffsetToPlainOffset(rawText, rawOffset) {
  const src = String(rawText || "");
  const limit = Math.max(0, Math.min(src.length, Number(rawOffset) || 0));
  let plainOffset = 0;
  let rawCursor = 0;
  HL_MARKUP_RE.lastIndex = 0;
  let match;
  while ((match = HL_MARKUP_RE.exec(src)) !== null) {
    const fullStart = match.index;
    const prefixLen = match[1] ? `==[${match[1]}]`.length : 2;
    const contentStart = fullStart + prefixLen;
    const contentEnd = contentStart + match[2].length;
    const fullEnd = HL_MARKUP_RE.lastIndex;

    if (limit <= fullStart) {
      return plainOffset + (limit - rawCursor);
    }
    plainOffset += fullStart - rawCursor;

    if (limit <= contentStart) {
      return plainOffset;
    }
    if (limit <= contentEnd) {
      return plainOffset + (limit - contentStart);
    }
    plainOffset += match[2].length;
    rawCursor = fullEnd;
    if (limit <= fullEnd) {
      return plainOffset;
    }
  }
  return plainOffset + Math.max(0, limit - rawCursor);
}

export function renderHighlightedText(container, rawText) {
  container.replaceChildren();
  for (const seg of parseHighlightSegments(rawText)) {
    if (!seg.color) {
      container.appendChild(document.createTextNode(seg.text));
    } else {
      const mark = document.createElement("mark");
      mark.className = `note-inline-hl hl-${seg.color}`;
      mark.textContent = seg.text;
      container.appendChild(mark);
    }
  }
}

export function applyRangeHighlightMarkup(rawText, plainStart, plainEnd, newColor) {
  const segments = parseHighlightSegments(rawText);
  const chars = [];
  const colors = [];
  for (const seg of segments) {
    for (const ch of seg.text) {
      chars.push(ch);
      colors.push(seg.color);
    }
  }
  const start = Math.max(0, Math.min(chars.length, Math.floor(plainStart)));
  const end = Math.max(start, Math.min(chars.length, Math.floor(plainEnd)));
  if (end <= start) return String(rawText || "");

  const resolvedColor = HIGHLIGHT_COLORS.includes(newColor) ? newColor : null;
  for (let i = start; i < end; i++) {
    colors[i] = resolvedColor;
  }

  let out = "";
  let i = 0;
  while (i < chars.length) {
    const col = colors[i];
    let j = i + 1;
    while (j < chars.length && colors[j] === col) j++;
    const slice = chars.slice(i, j).join("");
    out += col ? `==[${col}]${slice}==` : slice;
    i = j;
  }
  return out;
}

export function unwrapUserHighlights(rootEl) {
  if (!rootEl) return;
  rootEl.querySelectorAll("mark.user-hl").forEach((mark) => {
    const parent = mark.parentNode;
    if (!parent) return;
    while (mark.firstChild) {
      parent.insertBefore(mark.firstChild, mark);
    }
    mark.remove();
    parent.normalize();
  });
}

export function applyQuoteHighlightToElements(containers, quoteText, { color = "yellow", noteId = null } = {}) {
  const cleanQuery = stripHighlightMarkup(quoteText).trim();
  const safeColor = HIGHLIGHT_COLORS.includes(color) ? color : null;
  if (!cleanQuery || !safeColor || !containers?.length) return false;

  const entries = [];
  let totalLen = 0;
  let concat = "";

  for (const root of containers) {
    if (!root) continue;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.nodeValue) return NodeFilter.FILTER_REJECT;
        const p = node.parentElement;
        if (p?.closest?.("rt, rp, .sent-ask-btn, script, style")) {
          return NodeFilter.FILTER_REJECT;
        }
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    let textNode;
    while ((textNode = walker.nextNode())) {
      const val = textNode.nodeValue;
      entries.push({
        node: textNode,
        start: totalLen,
        end: totalLen + val.length,
      });
      concat += val;
      totalLen += val.length;
    }
  }

  const matchStart = concat.indexOf(cleanQuery);
  if (matchStart < 0) return false;
  const matchEnd = matchStart + cleanQuery.length;

  const slices = [];
  for (const entry of entries) {
    if (entry.end <= matchStart || entry.start >= matchEnd) continue;
    slices.push({
      node: entry.node,
      localStart: Math.max(0, matchStart - entry.start),
      localEnd: Math.min(entry.end - entry.start, matchEnd - entry.start),
    });
  }
  if (!slices.length) return false;

  for (const s of slices) {
    const len = s.node.nodeValue.length;
    let target = s.node;
    if (s.localEnd < len) {
      target.splitText(s.localEnd);
    }
    if (s.localStart > 0) {
      target = target.splitText(s.localStart);
    }
    const mark = document.createElement("mark");
    mark.className = `user-hl hl-${safeColor}`;
    if (noteId != null) mark.dataset.noteId = String(noteId);
    target.parentNode.insertBefore(mark, target);
    mark.appendChild(target);
  }
  return true;
}
