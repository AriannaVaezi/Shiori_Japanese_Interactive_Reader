/** Chapter reader view, vertical/horizontal layouts, furigana modes, and scroll persistence. */

import { api } from "./api.js";
import {
  closeGrammarDrawer,
  dockGrammarDrawer,
  loadChapterGrammarThreads,
  openGrammarForSentence,
} from "./grammar.js";
import { closeLookup, openSelectionLookup, openWordLookup } from "./lookup.js";
import { closeTimerPopover } from "./timer.js";
import {
  HIGHLIGHT_COLORS,
  applyQuoteHighlightToElements,
  extractCleanText,
  extractRangeCleanText,
  state,
  unwrapUserHighlights,
} from "./state.js";

const libraryView = document.getElementById("library-view");
const readerView = document.getElementById("reader-view");
const mangaView = document.getElementById("manga-view");
const mangaTokensEl = document.getElementById("manga-text-tokens");
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
const btnHighlights = document.getElementById("btn-toggle-highlights");
const btnMangaMarks = document.getElementById("btn-manga-hide-marks");

const viewport = document.getElementById("reader-viewport");
const contentEl = document.getElementById("reader-content");
const selectionTooltip = document.getElementById("selection-tooltip");
const btnSelLookup = document.getElementById("btn-sel-lookup");
const btnSelTranslate = document.getElementById("btn-sel-translate");
const btnSelChat = document.getElementById("btn-sel-chat");
const btnSelSaveNote = document.getElementById("btn-sel-save-note");
const btnSelAskGrammar = document.getElementById("btn-sel-ask-grammar");
const btnSelUnhighlight = document.getElementById("btn-sel-unhighlight");

let onBackLibraryCb = null;
let onChapterQuizCb = null;
let onSaveNoteCb = null;
let onChapterOpenedCb = null;
let onOpenMangaCb = null;
let onHighlightCreatedCb = null;
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
  const hideMarks = String(s.hide_annotations || "false") === "true";

  document.documentElement.dataset.theme = theme;
  document.documentElement.dataset.furigana = furi;
  document.documentElement.dataset.hideAnnotations = hideMarks ? "true" : "false";
  document.documentElement.style.setProperty("--reader-font-size", `${fontSize}px`);

  viewport.classList.toggle("mode-vertical", writing === "vertical");
  viewport.classList.toggle("mode-horizontal", writing !== "vertical");

  btnWriting.textContent = writing === "vertical" ? "縦 Vertical" : "横 Horizontal";
  btnFurigana.textContent = FURIGANA_LABELS[furi];
  btnTheme.textContent = THEME_LABELS[theme];
  const marksLabel = hideMarks ? "Marks: Hidden" : "Marks: Shown";
  if (btnHighlights) {
    btnHighlights.textContent = marksLabel;
    btnHighlights.classList.toggle("btn-primary", hideMarks);
  }
  if (btnMangaMarks) {
    btnMangaMarks.textContent = marksLabel;
    btnMangaMarks.classList.toggle("btn-primary", hideMarks);
  }
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

async function flushProgress(extraDelta = false, { keepalive = false } = {}) {
  if (!state.currentBook || !state.currentChapter || readerView.hidden) return;
  const now = Date.now();
  let deltaSec = 0;
  if (extraDelta && document.visibilityState === "visible" && now - state.lastActivityMs < 65000) {
    deltaSec = Math.min(60, Math.max(0, Math.round((now - lastProgressSavedMs) / 1000)));
  }
  lastProgressSavedMs = now;
  try {
    await api.saveProgress(
      state.currentBook.id,
      {
        chapter_index: state.currentChapter.chapter_index,
        scroll_ratio: getScrollRatio(),
        reading_seconds_delta: deltaSec,
      },
      { keepalive }
    );
  } catch {
    // ignore transient progress save errors
  }
}

function scheduleProgressSave() {
  state.lastActivityMs = Date.now();
  clearTimeout(saveScrollTimer);
  saveScrollTimer = setTimeout(() => flushProgress(true), 700);
}

export function hideSelectionTooltip() {
  selectionTooltip.hidden = true;
  selectionTooltip.classList.remove("touch-docked");
  document.documentElement.classList.remove("sel-tooltip-docked");
  pendingSelection = null;
}

function isTouchOrNarrowViewport() {
  return window.matchMedia?.("(pointer: coarse)")?.matches || window.innerWidth <= 768;
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

function applyChapterNoteHighlights(notes) {
  unwrapUserHighlights(contentEl);
  contentEl.querySelectorAll(".sent").forEach((el) => {
    el.classList.remove("has-note", ...HIGHLIGHT_COLORS.map((c) => `hl-${c}`));
    delete el.dataset.noteId;
  });

  const sentList = [...contentEl.querySelectorAll(".sent")];
  for (const note of notes || []) {
    const color = HIGHLIGHT_COLORS.includes(note.color) ? note.color : "";
    if (!color) continue;
    const sentEl = note.sent_id
      ? sentList.find((el) => el.dataset.sentId === note.sent_id) || null
      : null;

    const wrapped =
      note.quote_text &&
      applyQuoteHighlightToElements([contentEl], note.quote_text, {
        color,
        noteId: note.id,
        anchorEl: sentEl,
      });
    if (!wrapped && sentEl) {
      sentEl.classList.remove(...HIGHLIGHT_COLORS.map((c) => `hl-${c}`));
      sentEl.classList.add("has-note", `hl-${color}`);
      sentEl.dataset.noteId = String(note.id);
    }
  }
}

export async function refreshChapterHighlights() {
  if (!state.currentBook || !state.currentChapter || readerView.hidden) return;
  try {
    const [troubleRes, notesRes] = await Promise.all([
      api.getTrouble({
        bookId: state.currentBook.id,
        includeRetired: false,
        limit: 200,
      }),
      api.listNotes({
        bookId: state.currentBook.id,
        chapterIndex: state.currentChapter.chapter_index,
        limit: 200,
      }),
    ]);
    const active = troubleRes.items || [];
    const troubleKeys = active
      .filter((i) => i.item_type === "word" && (i.memory_stage || 0) === 0)
      .map((i) => i.item_key);
    const stickyKeys = active
      .filter((i) => i.item_type === "word" && i.trouble_score >= 3.0)
      .map((i) => i.item_key);
    applyTokenHighlights(troubleKeys, stickyKeys);
    applyChapterNoteHighlights([...(notesRes.notes || [])].reverse());
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

  let chapter;
  try {
    chapter = await api.getChapter(bookId, chapterIndex);
  } catch (err) {
    contentEl.replaceChildren();
    const alert = document.createElement("div");
    alert.className = "inline-alert";
    alert.textContent = `Could not load chapter: ${err.message}`;
    contentEl.appendChild(alert);
    return;
  }
  state.currentChapter = chapter;
  history.replaceState(null, "", `#book/${bookId}/${chapter.chapter_index}`);
  contentEl.innerHTML = chapter.html || "";

  applyTokenHighlights(
    chapter.trouble_keys || [],
    chapter.sticky_keys || []
  );
  applyChapterNoteHighlights(chapter.chapter_notes || []);
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
  if (book.media_type === "manga" && onOpenMangaCb) {
    return onOpenMangaCb(bookId, startChapter);
  }
  state.currentBook = book;
  state.sessionStartedAt = new Date().toISOString();
  bookTitleEl.textContent = book.title;

  chapterSelect.replaceChildren();
  for (const ch of book.chapters || []) {
    const opt = document.createElement("option");
    opt.value = String(ch.chapter_index);
    opt.textContent = `${ch.chapter_index + 1}. ${ch.title}`;
    chapterSelect.appendChild(opt);
  }

  libraryView.hidden = true;
  if (mangaView) mangaView.hidden = true;
  document.documentElement.classList.remove("manga-fullscreen");
  dockGrammarDrawer(document.getElementById("reader-workspace"));
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

export function scrollToHighlight({ noteId = null, sentId = null } = {}) {
  const marks =
    noteId != null
      ? [...contentEl.querySelectorAll(`mark.user-hl[data-note-id="${CSS.escape(String(noteId))}"]`)]
      : [];
  const flashTargets = marks.length
    ? marks
    : sentId
      ? [...contentEl.querySelectorAll(`.sent[data-sent-id="${CSS.escape(sentId)}"]`)]
      : [];
  if (!flashTargets.length) return;
  flashTargets[0].scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
  for (const el of flashTargets) {
    el.classList.remove("hl-jump-flash");
    void el.offsetWidth;
    el.classList.add("hl-jump-flash");
    setTimeout(() => el.classList.remove("hl-jump-flash"), 1600);
  }
}

export function scrollToSentence(sentId) {
  scrollToHighlight({ sentId });
}

export function initReader({
  onBackToLibrary,
  onChapterQuiz,
  onSaveNote,
  onChapterOpened,
  onOpenManga,
  onHighlightCreated,
}) {
  onBackLibraryCb = onBackToLibrary;
  onChapterQuizCb = onChapterQuiz;
  onSaveNoteCb = onSaveNote;
  onChapterOpenedCb = onChapterOpened;
  onOpenMangaCb = onOpenManga;
  onHighlightCreatedCb = onHighlightCreated;

  const btnChapterQuiz = document.getElementById("btn-chapter-quiz");
  btnChapterQuiz?.addEventListener("click", () => {
    if (onChapterQuizCb) onChapterQuizCb("chapter");
  });

  const btnDoneReading = document.getElementById("btn-done-reading");
  btnDoneReading?.addEventListener("click", async () => {
    await flushProgress(true);
    closeLookup();
    hideSelectionTooltip();
    if (onChapterQuizCb) onChapterQuizCb("session");
  });

  btnBack.addEventListener("click", async () => {
    await flushProgress(true);
    closeLookup();
    closeTimerPopover();
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

  btnHighlights?.addEventListener("click", () => {
    const nextHidden = String(state.settings.hide_annotations || "false") !== "true";
    if (nextHidden) {
      closeLookup();
      closeGrammarDrawer();
      hideSelectionTooltip();
    }
    persistVisualPref({ hide_annotations: nextHidden ? "true" : "false" });
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
    if (sel && !sel.isCollapsed && sel.toString().trim().length >= 1) return;

    const tokEl = e.target.closest?.(".tok");
    if (tokEl) {
      hideSelectionTooltip();
      openWordLookup(tokEl);
    }
  });

  // Passage selection bar for Look Up, Chat, Grammar, or saving quotes/notes (mouse + touch)
  let selDebounceTimer = null;
  let touchingTooltip = false;

  function inspectReaderSelection() {
    const inReader = !readerView.hidden;
    const inManga = mangaView && !mangaView.hidden;
    if (!inReader && !inManga) return;
    const activeContainer = inReader ? contentEl : mangaTokensEl;
    if (!activeContainer) return;

    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) {
      hideSelectionTooltip();
      return;
    }

    const range = sel.getRangeAt(0);
    if (
      !activeContainer.contains(range.startContainer) ||
      !activeContainer.contains(range.endContainer)
    ) {
      hideSelectionTooltip();
      return;
    }

    const cleanTarget = extractRangeCleanText(range);
    if (
      cleanTarget.length < 1 ||
      cleanTarget.length > 1500 ||
      /^[\s。、！？!?「」『』（）()【】・…—―\-]+$/.test(cleanTarget)
    ) {
      hideSelectionTooltip();
      return;
    }

    const startNode = range.startContainer;
    const startEl = startNode.nodeType === Node.ELEMENT_NODE ? startNode : startNode.parentElement;
    const common = range.commonAncestorContainer;
    const commonEl = common.nodeType === Node.ELEMENT_NODE ? common : common.parentElement;
    let intersectingSents = [];
    if (inReader && typeof range.intersectsNode === "function") {
      intersectingSents = [...contentEl.querySelectorAll(".sent")].filter((el) =>
        range.intersectsNode(el)
      );
    }
    let sentEl = commonEl?.closest?.(".sent") || startEl?.closest?.(".sent") || intersectingSents[0] || null;
    const resolvedSentId = inManga
      ? mangaTokensEl?.dataset.sentId || null
      : sentEl?.dataset.sentId || null;
    const sentenceContextText =
      intersectingSents.length > 1
        ? intersectingSents.map((el) => extractCleanText(el)).join(" ")
        : sentEl
          ? extractCleanText(sentEl)
          : cleanTarget;

    const touchedNoteIds = new Set();
    if (typeof range.intersectsNode === "function") {
      activeContainer.querySelectorAll("mark.user-hl[data-note-id]").forEach((m) => {
        if (range.intersectsNode(m)) {
          const id = Number(m.dataset.noteId);
          if (id) touchedNoteIds.add(id);
        }
      });
    }
    if (sentEl?.dataset.noteId) {
      const sid = Number(sentEl.dataset.noteId);
      if (sid) touchedNoteIds.add(sid);
    }

    suppressSelectionClickUntil = Date.now() + 250;
    const rect = range.getBoundingClientRect();
    const anchorRect = {
      left: rect.left,
      right: rect.right,
      top: rect.top,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
    };
    pendingSelection = {
      sentId: resolvedSentId,
      sentenceText: sentenceContextText,
      targetText: cleanTarget,
      touchedNoteIds: [...touchedNoteIds],
      anchorRect,
    };

    btnSelLookup.hidden = cleanTarget.length > 40;
    if (btnSelTranslate) btnSelTranslate.hidden = cleanTarget.length > 800;
    btnSelChat.hidden = cleanTarget.length > 400;
    btnSelAskGrammar.hidden = !(sentEl && cleanTarget.length <= 200);
    btnSelSaveNote.hidden = false;

    if (isTouchOrNarrowViewport()) {
      selectionTooltip.classList.add("touch-docked");
      document.documentElement.classList.add("sel-tooltip-docked");
      selectionTooltip.style.left = "";
      selectionTooltip.style.top = "";
      selectionTooltip.hidden = false;
    } else {
      selectionTooltip.classList.remove("touch-docked");
      document.documentElement.classList.remove("sel-tooltip-docked");
      selectionTooltip.hidden = false;
      const tipRect = selectionTooltip.getBoundingClientRect();
      const left = Math.min(
        window.innerWidth - tipRect.width - 12,
        Math.max(12, rect.left + rect.width / 2 - tipRect.width / 2)
      );
      const top = Math.max(60, rect.top - tipRect.height - 8);
      selectionTooltip.style.left = `${Math.round(left)}px`;
      selectionTooltip.style.top = `${Math.round(top)}px`;
    }
  }

  selectionTooltip.addEventListener("pointerdown", () => {
    touchingTooltip = true;
    setTimeout(() => {
      touchingTooltip = false;
    }, 350);
  });
  selectionTooltip.addEventListener("mousedown", (e) => {
    e.preventDefault();
  });

  document.addEventListener("mouseup", (e) => {
    const inReader = !readerView.hidden;
    const inManga = mangaView && !mangaView.hidden;
    if (!inReader && !inManga) return;
    const activeContainer = inReader ? contentEl : mangaTokensEl;
    if (!activeContainer || !activeContainer.contains(e.target)) {
      if (!selectionTooltip.contains(e.target)) hideSelectionTooltip();
      return;
    }
    inspectReaderSelection();
  });

  document.addEventListener("selectionchange", () => {
    const inReader = !readerView.hidden;
    const inManga = mangaView && !mangaView.hidden;
    if (!inReader && !inManga) return;
    clearTimeout(selDebounceTimer);
    selDebounceTimer = setTimeout(() => {
      if (touchingTooltip) return;
      inspectReaderSelection();
    }, 160);
  });

  document.addEventListener("pointerdown", (e) => {
    if (selectionTooltip.hidden) return;
    if (selectionTooltip.contains(e.target)) return;
    const inReader = !readerView.hidden;
    const inManga = mangaView && !mangaView.hidden;
    const activeContainer = inReader ? contentEl : mangaTokensEl;
    if (activeContainer && !activeContainer.contains(e.target)) {
      hideSelectionTooltip();
    }
  });

  btnSelLookup.addEventListener("click", () => {
    if (!pendingSelection) return;
    const payload = { ...pendingSelection };
    hideSelectionTooltip();
    window.getSelection()?.removeAllRanges();
    openSelectionLookup({
      text: payload.targetText,
      sentId: payload.sentId,
      sentenceText: payload.sentenceText,
      anchorRect: payload.anchorRect,
    });
  });

  btnSelTranslate?.addEventListener("click", () => {
    if (!pendingSelection) return;
    const payload = { ...pendingSelection };
    hideSelectionTooltip();
    window.getSelection()?.removeAllRanges();
    closeLookup();
    openGrammarForSentence({
      sentId: payload.sentId || "selection",
      sentenceText: payload.sentenceText,
      targetText: payload.targetText,
      autoAsk: true,
      mode: "translate",
    });
  });

  btnSelChat.addEventListener("click", () => {
    if (!pendingSelection) return;
    const payload = { ...pendingSelection };
    hideSelectionTooltip();
    window.getSelection()?.removeAllRanges();
    closeLookup();
    openGrammarForSentence({
      sentId: payload.sentId || "selection",
      sentenceText: payload.sentenceText,
      targetText: payload.targetText,
      autoAsk: false,
    });
  });

  btnSelSaveNote.addEventListener("click", () => {
    if (!pendingSelection || !onSaveNoteCb) return;
    const payload = { ...pendingSelection };
    hideSelectionTooltip();
    window.getSelection()?.removeAllRanges();
    closeLookup();
    onSaveNoteCb({
      bookId: state.currentBook?.id ?? null,
      chapterIndex: state.currentChapter?.chapter_index ?? null,
      sentId: payload.sentId,
      quoteText: payload.targetText,
      category: "quote",
    });
  });

  selectionTooltip.querySelectorAll(".sel-hl-swatches .hl-swatch-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!pendingSelection) return;
      const color = btn.dataset.color || "yellow";
      const payload = { ...pendingSelection };
      hideSelectionTooltip();
      window.getSelection()?.removeAllRanges();
      closeLookup();
      const bookId = state.currentBook?.id ?? null;
      const chapterIndex = state.currentChapter?.chapter_index ?? null;
      if (bookId == null || chapterIndex == null) return;
      try {
        await api.createNote({
          book_id: bookId,
          chapter_index: chapterIndex,
          sent_id: payload.sentId || null,
          quote_text: payload.targetText,
          note_text: "",
          category: "quote",
          color,
          replace_existing: true,
          target_note_ids: payload.touchedNoteIds || [],
        });
        if (onHighlightCreatedCb) onHighlightCreatedCb();
      } catch {
        // ignore transient create error
      }
    });
  });

  btnSelUnhighlight?.addEventListener("click", async () => {
    if (!pendingSelection) return;
    const payload = { ...pendingSelection };
    hideSelectionTooltip();
    window.getSelection()?.removeAllRanges();
    closeLookup();
    const bookId = state.currentBook?.id ?? null;
    const chapterIndex = state.currentChapter?.chapter_index ?? null;
    if (bookId == null || chapterIndex == null) return;
    try {
      await api.unhighlightNotes({
        book_id: bookId,
        chapter_index: chapterIndex,
        sent_id: payload.sentId || null,
        quote_text: payload.targetText,
        note_ids: payload.touchedNoteIds || [],
      });
      if (onHighlightCreatedCb) onHighlightCreatedCb();
    } catch {
      // ignore transient unhighlight error
    }
  });

  btnSelAskGrammar.addEventListener("click", () => {
    if (!pendingSelection || !pendingSelection.sentId) return;
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
    if (
      !document.getElementById("quiz-modal").hidden ||
      !document.getElementById("trouble-modal").hidden ||
      !document.getElementById("notes-modal").hidden ||
      !document.getElementById("settings-modal").hidden
    ) return;
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (e.key === "[") advanceChapter(-1);
    if (e.key === "]") advanceChapter(1);
    if (e.key === "h" || e.key === "H") {
      e.preventDefault();
      btnHighlights?.click();
    }
  });

  window.addEventListener("beforeunload", () => {
    flushProgress(true, { keepalive: true });
  });
  window.addEventListener("pagehide", () => {
    flushProgress(true, { keepalive: true });
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      flushProgress(true, { keepalive: true });
    }
  });
}
