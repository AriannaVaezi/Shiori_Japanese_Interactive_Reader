/** Chapter reader view, vertical/horizontal layouts, furigana modes, and scroll persistence. */

import { api } from "./api.js";
import { closeGrammarDrawer, loadChapterGrammarThreads, openGrammarForSentence } from "./grammar.js";
import { closeLookup, openWordLookup } from "./lookup.js";
import { extractCleanText, extractRangeCleanText, state } from "./state.js";

const libraryView = document.getElementById("library-view");
const readerView = document.getElementById("reader-view");
const bookTitleEl = document.getElementById("reader-book-title");
const chapterSelect = document.getElementById("chapter-select");
const btnBack = document.getElementById("btn-back-library");
const btnPrev = document.getElementById("btn-prev-chap");
const btnNext = document.getElementById("btn-next-chap");
const btnFooterPrev = document.getElementById("btn-footer-prev");
const btnFooterNext = document.getElementById("btn-footer-next");
const footerNav = document.getElementById("chapter-footer");
const footerLabel = document.getElementById("footer-chap-label");

const btnWriting = document.getElementById("btn-toggle-writing");
const btnFurigana = document.getElementById("btn-cycle-furigana");
const btnFontDown = document.getElementById("btn-font-down");
const btnFontUp = document.getElementById("btn-font-up");
const btnTheme = document.getElementById("btn-cycle-theme");

const viewport = document.getElementById("reader-viewport");
const contentEl = document.getElementById("reader-content");
const selectionTooltip = document.getElementById("selection-tooltip");

let onBackLibraryCb = null;
let onChapterQuizCb = null;
let onChapterOpenedCb = null;
let pendingSelection = null;
let saveScrollTimer = null;
let lastProgressSavedMs = Date.now();
let suppressSelectionClickUntil = 0;

const FURIGANA_MODES = ["all", "trouble", "hover", "off"];
const FURIGANA_LABELS = {
  all: "振り仮名: All",
  trouble: "振り仮名: Trouble",
  hover: "振り仮名: Hover",
  off: "振り仮名: Off",
};
const THEME_MODES = ["paper", "sepia", "dark"];
const THEME_LABELS = { paper: "Paper", sepia: "Sepia", dark: "Dark" };

export function applyVisualSettings() {
  const s = state.settings;
  const theme = THEME_MODES.includes(s.theme) ? s.theme : "paper";
  const furi = FURIGANA_MODES.includes(s.furigana_mode) ? s.furigana_mode : "all";
  const writing = s.reading_mode === "vertical" ? "vertical" : "horizontal";
  const fontSize = Math.max(14, Math.min(32, parseInt(s.font_size || "20", 10) || 20));

  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.furigana = furi;
  document.documentElement.style.setProperty("--reader-font-size", `${fontSize}px`);

  viewport.classList.toggle("mode-vertical", writing === "vertical");
  viewport.classList.toggle("mode-horizontal", writing !== "vertical");

  btnWriting.textContent = writing === "vertical" ? "縦 Vertical" : "横 Horizontal";
  btnFurigana.textContent = FURIGANA_LABELS[furi];
  btnTheme.textContent = THEME_LABELS[theme];
}

function getScrollRatio() {
  if (state.settings.reading_mode === "vertical") {
    const maxScroll = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    if (maxScroll <= 0) return 0;
    return Math.min(1, Math.max(0, Math.abs(viewport.scrollLeft) / maxScroll));
  }
  const maxScroll = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
  if (maxScroll <= 0) return 0;
  return Math.min(1, Math.max(0, viewport.scrollTop / maxScroll));
}

function setScrollRatio(ratio) {
  const clamped = Math.min(1, Math.max(0, Number(ratio) || 0));
  if (state.settings.reading_mode === "vertical") {
    const maxScroll = Math.max(0, viewport.scrollWidth - viewport.clientWidth);
    viewport.scrollTop = 0;
    viewport.scrollLeft = -(clamped * maxScroll);
  } else {
    const maxScroll = Math.max(0, viewport.scrollHeight - viewport.clientHeight);
    viewport.scrollLeft = 0;
    viewport.scrollTop = clamped * maxScroll;
  }
}

async function flushProgress(extraDelta = false) {
  if (!state.currentBook || !state.currentChapter || readerView.hidden) return;
  const now = Date.now();
  let deltaSec = 0;
  if (extraDelta && document.visibilityState === "visible" && now - state.lastActivityMs < 65000) {
    deltaSec = Math.min(60, Math.max(0, Math.round((now - lastProgressSavedMs) / 1000)));
  }
  lastProgressSavedMs = now;
  try {
    await api.saveProgress(state.currentBook.id, {
      chapter_index: state.currentChapter.chapter_index,
      scroll_ratio: getScrollRatio(),
      reading_seconds_delta: deltaSec,
    });
  } catch {
    // ignore transient progress save errors
  }
}

function scheduleProgressSave() {
  state.lastActivityMs = Date.now();
  clearTimeout(saveScrollTimer);
  saveScrollTimer = setTimeout(() => flushProgress(true), 700);
}

function hideSelectionTooltip() {
  selectionTooltip.hidden = true;
  pendingSelection = null;
}

function applyTokenHighlights(troubleKeys, stickyKeys) {
  const troubleSet = new Set(troubleKeys || []);
  const stickySet = new Set(stickyKeys || []);
  contentEl.querySelectorAll(".tok").forEach((el) => {
    const key = `${el.dataset.lemma || ""}|${el.dataset.reading || ""}`;
    el.classList.toggle("trouble-word", troubleSet.has(key));
    el.classList.toggle("sticky-word", stickySet.has(key));
  });
}

export async function refreshChapterHighlights() {
  if (!state.currentBook || !state.currentChapter) return;
  try {
    const res = await api.getTrouble({
      bookId: state.currentBook.id,
      includeRetired: false,
      limit: 200,
    });
    const active = res.items || [];
    const troubleKeys = active.filter((i) => i.item_type === "word").map((i) => i.item_key);
    const stickyKeys = active
      .filter((i) => i.item_type === "word" && i.trouble_score >= 3.0)
      .map((i) => i.item_key);
    applyTokenHighlights(troubleKeys, stickyKeys);
  } catch {
    // ignore transient highlight refresh error
  }
}

function updateNavControls() {
  if (!state.currentBook || !state.currentChapter) return;
  const idx = state.currentChapter.chapter_index;
  const total = state.currentChapter.total_chapters;
  chapterSelect.value = String(idx);
  btnPrev.disabled = idx <= 0;
  btnFooterPrev.disabled = idx <= 0;
  btnNext.disabled = idx >= total - 1;
  btnFooterNext.disabled = idx >= total - 1;
  footerLabel.textContent = `${state.currentChapter.title} (${idx + 1} / ${total})`;
  footerNav.hidden = false;
}

export async function openChapter(bookId, chapterIndex, restoreScrollRatio = 0) {
  closeLookup();
  hideSelectionTooltip();

  contentEl.innerHTML = '<p class="loading-line">Loading chapter…</p>';
  footerNav.hidden = true;

  const chapter = await api.getChapter(bookId, chapterIndex);
  state.currentChapter = chapter;
  contentEl.innerHTML = chapter.html || "";

  applyTokenHighlights(chapter.trouble_keys || [], chapter.sticky_keys || []);
  await loadChapterGrammarThreads(bookId, chapterIndex);
  updateNavControls();
  if (onChapterOpenedCb) onChapterOpenedCb();

  requestAnimationFrame(() => {
    setScrollRatio(restoreScrollRatio);
  });
  lastProgressSavedMs = Date.now();
  await flushProgress(false);
}

export async function openBook(bookId, startChapter = 0, startScroll = 0) {
  const book = await api.getBook(bookId);
  state.currentBook = book;
  bookTitleEl.textContent = book.title;

  chapterSelect.replaceChildren();
  for (const ch of book.chapters || []) {
    const opt = document.createElement("option");
    opt.value = String(ch.chapter_index);
    opt.textContent = `${ch.chapter_index + 1}. ${ch.title}`;
    chapterSelect.appendChild(opt);
  }

  libraryView.hidden = true;
  readerView.hidden = false;
  closeGrammarDrawer();
  applyVisualSettings();

  await openChapter(bookId, startChapter, startScroll);
}

async function persistVisualPref(updates) {
  Object.assign(state.settings, updates);
  applyVisualSettings();
  try {
    const saved = await api.updateSettings(updates);
    state.settings = { ...state.settings, ...saved };
  } catch {
    // keep optimistic UI update
  }
}

export function advanceChapter(delta) {
  if (!state.currentBook || !state.currentChapter) return;
  const nextIdx = state.currentChapter.chapter_index + delta;
  if (nextIdx >= 0 && nextIdx < state.currentChapter.total_chapters) {
    openChapter(state.currentBook.id, nextIdx, 0);
  }
}

export function initReader({ onBackToLibrary, onChapterQuiz, onChapterOpened }) {
  onBackLibraryCb = onBackToLibrary;
  onChapterQuizCb = onChapterQuiz;
  onChapterOpenedCb = onChapterOpened;

  const btnChapterQuiz = document.getElementById("btn-chapter-quiz");
  btnChapterQuiz?.addEventListener("click", () => {
    if (onChapterQuizCb) onChapterQuizCb("chapter");
  });

  btnBack.addEventListener("click", async () => {
    await flushProgress(true);
    closeLookup();
    closeGrammarDrawer();
    hideSelectionTooltip();
    readerView.hidden = true;
    if (onBackLibraryCb) onBackLibraryCb();
  });

  btnPrev.addEventListener("click", () => advanceChapter(-1));
  btnFooterPrev.addEventListener("click", () => advanceChapter(-1));
  btnNext.addEventListener("click", () => advanceChapter(1));
  btnFooterNext.addEventListener("click", () => advanceChapter(1));

  chapterSelect.addEventListener("change", () => {
    if (!state.currentBook) return;
    const idx = parseInt(chapterSelect.value, 10);
    if (!Number.isNaN(idx)) {
      openChapter(state.currentBook.id, idx, 0);
    }
  });

  btnWriting.addEventListener("click", async () => {
    const ratio = getScrollRatio();
    const nextMode = state.settings.reading_mode === "vertical" ? "horizontal" : "vertical";
    await persistVisualPref({ reading_mode: nextMode });
    requestAnimationFrame(() => setScrollRatio(ratio));
  });

  btnFurigana.addEventListener("click", () => {
    const cur = state.settings.furigana_mode || "all";
    const next = FURIGANA_MODES[(FURIGANA_MODES.indexOf(cur) + 1) % FURIGANA_MODES.length];
    persistVisualPref({ furigana_mode: next });
  });

  btnTheme.addEventListener("click", () => {
    const cur = state.settings.theme || "paper";
    const next = THEME_MODES[(THEME_MODES.indexOf(cur) + 1) % THEME_MODES.length];
    persistVisualPref({ theme: next });
  });

  btnFontDown.addEventListener("click", () => {
    const cur = parseInt(state.settings.font_size || "20", 10) || 20;
    persistVisualPref({ font_size: String(Math.max(14, cur - 2)) });
  });

  btnFontUp.addEventListener("click", () => {
    const cur = parseInt(state.settings.font_size || "20", 10) || 20;
    persistVisualPref({ font_size: String(Math.min(32, cur + 2)) });
  });

  // Delegated click: ? grammar button or word token
  contentEl.addEventListener("click", (e) => {
    state.lastActivityMs = Date.now();
    const askBtn = e.target.closest?.(".sent-ask-btn");
    if (askBtn) {
      e.stopPropagation();
      hideSelectionTooltip();
      closeLookup();
      const sentEl = askBtn.closest(".sent");
      const sentId = askBtn.dataset.sentId;
      if (sentEl && sentId) {
        openGrammarForSentence({
          sentId,
          sentenceText: extractCleanText(sentEl),
          targetText: "",
        });
      }
      return;
    }

    if (Date.now() < suppressSelectionClickUntil) return;
    const sel = window.getSelection();
    if (sel && sel.toString().trim().length > 1) return;

    const tokEl = e.target.closest?.(".tok");
    if (tokEl) {
      hideSelectionTooltip();
      openWordLookup(tokEl);
    }
  });

  // Phrase selection pill for multi-word grammar questions
  document.addEventListener("mouseup", (e) => {
    if (readerView.hidden) return;
    if (!contentEl.contains(e.target)) {
      if (!selectionTooltip.contains(e.target)) hideSelectionTooltip();
      return;
    }

    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
      hideSelectionTooltip();
      return;
    }

    const range = sel.getRangeAt(0);
    const cleanTarget = extractRangeCleanText(range);
    if (cleanTarget.length < 2 || cleanTarget.length > 60) {
      hideSelectionTooltip();
      return;
    }

    const common = range.commonAncestorContainer;
    const el = common.nodeType === Node.ELEMENT_NODE ? common : common.parentElement;
    const sentEl = el?.closest?.(".sent") || range.startContainer.parentElement?.closest?.(".sent");
    if (!sentEl || !sentEl.dataset.sentId) {
      hideSelectionTooltip();
      return;
    }

    suppressSelectionClickUntil = Date.now() + 250;
    const rect = range.getBoundingClientRect();
    pendingSelection = {
      sentId: sentEl.dataset.sentId,
      sentenceText: extractCleanText(sentEl),
      targetText: cleanTarget,
    };

    selectionTooltip.hidden = false;
    const tipRect = selectionTooltip.getBoundingClientRect();
    const left = Math.min(
      window.innerWidth - tipRect.width - 12,
      Math.max(12, rect.left + rect.width / 2 - tipRect.width / 2)
    );
    const top = Math.max(60, rect.top - tipRect.height - 8);
    selectionTooltip.style.left = `${Math.round(left)}px`;
    selectionTooltip.style.top = `${Math.round(top)}px`;
  });

  selectionTooltip.addEventListener("click", () => {
    if (!pendingSelection) return;
    const payload = { ...pendingSelection };
    hideSelectionTooltip();
    window.getSelection()?.removeAllRanges();
    closeLookup();
    openGrammarForSentence(payload);
  });

  // Mouse wheel vertical->horizontal scroll mapping in vertical-rl mode
  viewport.addEventListener(
    "wheel",
    (e) => {
      if (state.settings.reading_mode !== "vertical") return;
      if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
        e.preventDefault();
        viewport.scrollLeft -= e.deltaY;
      }
    },
    { passive: false }
  );

  viewport.addEventListener("scroll", scheduleProgressSave);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeLookup();
      hideSelectionTooltip();
      return;
    }
    if (readerView.hidden) return;
    if (!document.getElementById("quiz-modal").hidden || !document.getElementById("trouble-modal").hidden) return;
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (e.key === "[") advanceChapter(-1);
    if (e.key === "]") advanceChapter(1);
  });

  window.addEventListener("beforeunload", () => {
    flushProgress(true);
  });
}
