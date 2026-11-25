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
