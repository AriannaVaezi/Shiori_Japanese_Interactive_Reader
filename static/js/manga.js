/** Manga reader view: RTL 2-page spreads, 1-page horizontal, 1-page vertical scroll, exact aspect ratios, fullscreen, and CPU OCR overlays. */

import { api } from "./api.js";
import {
  closeGrammarDrawer,
  dockGrammarDrawer,
  loadChapterGrammarThreads,
  openGrammarForSentence,
  toggleGrammarDrawer,
} from "./grammar.js";
import { closeLookup, openWordLookup } from "./lookup.js";
import { hideSelectionTooltip } from "./reader.js";
import {
  HIGHLIGHT_COLORS,
  applyQuoteHighlightToElements,
  extractCleanText,
  state,
  stripHighlightMarkup,
  unwrapUserHighlights,
} from "./state.js";

const libraryView = document.getElementById("library-view");
const readerView = document.getElementById("reader-view");
const mangaView = document.getElementById("manga-view");
const mangaWorkspace = document.getElementById("manga-workspace");
const mangaViewport = document.getElementById("manga-viewport");
const mangaStage = document.getElementById("manga-stage");
const mangaBookTitle = document.getElementById("manga-book-title");
const pageInput = document.getElementById("manga-page-input");
const pageTotalEl = document.getElementById("manga-page-total");
const tocSelect = document.getElementById("manga-toc-select");

const btnBack = document.getElementById("btn-manga-back");
const btnPrev = document.getElementById("btn-manga-prev");
const btnNext = document.getElementById("btn-manga-next");
const btnLayout = document.getElementById("btn-manga-layout");
const btnOffset = document.getElementById("btn-manga-offset");
const btnOcrMode = document.getElementById("btn-manga-ocr-mode");
const btnHideMarks = document.getElementById("btn-manga-hide-marks");
const btnReaderHighlights = document.getElementById("btn-toggle-highlights");
const btnScanOcr = document.getElementById("btn-manga-scan-ocr");
const btnCrop = document.getElementById("btn-manga-crop");
const btnScreenshot = document.getElementById("btn-manga-screenshot");
const shotPopover = document.getElementById("manga-shot-popover");
const shotActionsEl = document.getElementById("manga-shot-actions");
const btnTrouble = document.getElementById("btn-manga-trouble");
const btnNotes = document.getElementById("btn-manga-notes");
const btnGrammar = document.getElementById("btn-manga-grammar");
const btnSettings = document.getElementById("btn-manga-settings");
const btnFullscreen = document.getElementById("btn-manga-fullscreen");

const ocrStatusEl = document.getElementById("manga-ocr-status");
const scrubberBar = document.getElementById("manga-scrubber-bar");
const pageSlider = document.getElementById("manga-page-slider");
const scrubberLabel = document.getElementById("manga-scrubber-label");
const textBar = document.getElementById("manga-text-bar");
const textSourceEl = document.getElementById("manga-text-source");
const textTokensEl = document.getElementById("manga-text-tokens");
const textEditWrap = document.getElementById("manga-text-edit-wrap");
const textEditInput = document.getElementById("manga-text-edit-input");
const btnEditApply = document.getElementById("btn-manga-edit-apply");
const btnBarTranslate = document.getElementById("btn-manga-bar-translate");
const btnBarChat = document.getElementById("btn-manga-bar-chat");
const btnBarGrammar = document.getElementById("btn-manga-bar-grammar");
const btnBarSave = document.getElementById("btn-manga-bar-save");
const btnBarUnhighlight = document.getElementById("btn-manga-unhighlight");
const btnBarEdit = document.getElementById("btn-manga-bar-edit");
const btnBarClose = document.getElementById("btn-manga-bar-close");

let mangaDetail = null;
let spreads = [];
let currentPageIndex = 0;
let sessionNonce = 0;
let loadedThreadKey = "";

const ocrCache = new Map(); // pageIndex -> ocr result
const mangaNotesByPage = new Map(); // pageIndex -> note[]
let ocrQueue = [];
let ocrRunning = false;
let cropMode = false;
let didJustDragCrop = false;
let pseudoFullscreen = false;
let activeInspector = null; // { pageIndex, sentId, text, tokens }

let progressTimer = null;
let lastProgressSavedMs = Date.now();
let verticalLoadObserver = null;
let verticalTrackObserver = null;
let suppressVerticalScrollTrack = false;
let statusHideTimer = null;
let isScrubbing = false;
let scrubTimer = null;

let onBackLibraryCb = null;
let onSaveNoteCb = null;
let onOpenTroubleCb = null;
let onOpenSettingsCb = null;
let onOpenNotesCb = null;
let onPageChangedCb = null;
let onHighlightCreatedCb = null;

const MOBILE_LAYOUT_STORAGE_KEY = "shiori_mobile_manga_layout";

const MANGA_LAYOUTS = ["spread", "single", "vertical"];
const MANGA_LAYOUT_LABELS = {
  spread: "2-Page Spread",
  single: "1-Page Horizontal",
  vertical: "1-Page Vertical",
};

const OCR_MODES = ["hover", "always", "off"];
const OCR_MODE_LABELS = {
  hover: "OCR: Hover",
  always: "OCR: Always",
  off: "OCR: Off",
};

let mangaZoom = 1;
let panX = 0;
let panY = 0;
let suppressNextMangaClick = false;
let suppressClickTimer = null;

function isMobileViewport() {
  return window.innerWidth <= 768;
}

function getLayoutMode() {
  if (isMobileViewport()) {
    const local = localStorage.getItem(MOBILE_LAYOUT_STORAGE_KEY) || "single";
    return MANGA_LAYOUTS.includes(local) ? local : "single";
  }
  const mode = state.settings.manga_layout || "spread";
  return MANGA_LAYOUTS.includes(mode) ? mode : "spread";
}

function getSpreadOffset() {
  return String(state.settings.manga_spread_offset ?? "1") === "0" ? 0 : 1;
}

function getOcrOverlayMode() {
  const mode = state.settings.manga_ocr_overlay || "hover";
  return OCR_MODES.includes(mode) ? mode : "hover";
}

function isFullscreenActive() {
  return Boolean(document.fullscreenElement) || pseudoFullscreen;
}

function syncFullscreenUi() {
  const active = isFullscreenActive() && !mangaView.hidden;
  document.documentElement.classList.toggle("manga-fullscreen", active);
  if (btnFullscreen) {
    btnFullscreen.textContent = active ? "⛶ Exit Full" : "⛶ Fullscreen";
  }
}

export async function toggleMangaFullscreen(forceState = null) {
  if (mangaView.hidden) return;
  const wantFullscreen = forceState != null ? Boolean(forceState) : !isFullscreenActive();

  if (wantFullscreen) {
    if (document.documentElement.requestFullscreen && !document.fullscreenElement) {
      try {
        await document.documentElement.requestFullscreen();
        pseudoFullscreen = false;
      } catch {
        pseudoFullscreen = true;
      }
    } else {
      pseudoFullscreen = true;
    }
  } else {
    pseudoFullscreen = false;
    if (document.fullscreenElement && document.exitFullscreen) {
      try {
        await document.exitFullscreen();
      } catch {
        // ignore exit error
      }
    }
  }
  syncFullscreenUi();
  requestAnimationFrame(layoutVisiblePages);
}

function setOcrStatus(text, autoHideMs = 0) {
  clearTimeout(statusHideTimer);
  if (!text) {
    ocrStatusEl.hidden = true;
    ocrStatusEl.textContent = "";
    return;
  }
  ocrStatusEl.textContent = text;
  ocrStatusEl.hidden = false;
  if (autoHideMs > 0) {
    statusHideTimer = setTimeout(() => {
      ocrStatusEl.hidden = true;
    }, autoHideMs);
  }
}

function computeSpreads(pages, offset) {
  const list = [];
  if (!pages || !pages.length) return list;

  let i = 0;
  if (offset === 1 && pages.length > 0 && !pages[0].is_spread) {
    list.push([pages[0]]);
    i = 1;
  }
  while (i < pages.length) {
    const p1 = pages[i];
    if (p1.is_spread || i + 1 >= pages.length) {
      list.push([p1]);
      i += 1;
      continue;
    }
    const p2 = pages[i + 1];
    if (p2.is_spread) {
      list.push([p1]);
      i += 1;
      continue;
    }
    list.push([p1, p2]);
    i += 2;
  }
  return list;
}

function findSpreadIndexForPage(spreadList, pageIdx) {
  for (let sIdx = 0; sIdx < spreadList.length; sIdx++) {
    if (spreadList[sIdx].some((p) => p.page_index === pageIdx)) {
      return sIdx;
    }
  }
  return 0;
}

function applyToolbarLabels() {
  const layout = getLayoutMode();
  const offset = getSpreadOffset();
  const ocrMode = getOcrOverlayMode();
  const hideMarks = String(state.settings.hide_annotations || "false") === "true";

  btnLayout.textContent = MANGA_LAYOUT_LABELS[layout];
  btnOffset.textContent = offset === 1 ? "Cover: Solo" : "Cover: Paired";
  btnOffset.hidden = layout !== "spread";
  btnOcrMode.textContent = OCR_MODE_LABELS[ocrMode];
  mangaStage.dataset.ocrOverlay = ocrMode;
  document.documentElement.dataset.hideAnnotations = hideMarks ? "true" : "false";
  const marksLabel = hideMarks ? "Marks: Hidden" : "Marks: Shown";
  if (btnHideMarks) {
    btnHideMarks.textContent = marksLabel;
    btnHideMarks.classList.toggle("btn-primary", hideMarks);
  }
  if (btnReaderHighlights) {
    btnReaderHighlights.textContent = marksLabel;
    btnReaderHighlights.classList.toggle("btn-primary", hideMarks);
  }
  btnCrop.classList.toggle("btn-primary", cropMode);
  btnCrop.textContent = cropMode ? "✂ Cancel Select" : "✂ Select Area";
  mangaViewport.classList.toggle("crop-active", cropMode);
}

async function persistMangaPref(updates) {
  Object.assign(state.settings, updates);
  applyToolbarLabels();
  try {
    const saved = await api.updateSettings(updates);
    state.settings = { ...state.settings, ...saved };
  } catch {
    // keep optimistic preference
  }
}

function syncTocSelect(pageIndex) {
  if (!tocSelect || tocSelect.hidden || !mangaDetail?.toc?.length) return;
  let activeVal = String(mangaDetail.toc[0].page_index);
  for (const entry of mangaDetail.toc) {
    if (entry.page_index <= pageIndex) {
      activeVal = String(entry.page_index);
    } else {
      break;
    }
  }
  tocSelect.value = activeVal;
}

function syncPageScrubber(pageIndex) {
  if (!mangaDetail) return;
  const total = Math.max(1, mangaDetail.page_count || 1);
  if (pageSlider) {
    pageSlider.min = "1";
    pageSlider.max = String(total);
    pageSlider.disabled = total <= 1;
    if (!isScrubbing) {
      pageSlider.value = String(pageIndex + 1);
    }
  }
  if (scrubberLabel) {
    scrubberLabel.textContent = `${pageIndex + 1} / ${total}`;
  }
}

async function setActivePageContext(pageIndex, reloadThreads = true) {
  if (!mangaDetail) return;
  const clamped = Math.max(0, Math.min(mangaDetail.page_count - 1, Number(pageIndex) || 0));
  currentPageIndex = clamped;
  history.replaceState(null, "", `#manga/${mangaDetail.id}/${clamped}`);

  state.currentChapter = {
    chapter_index: clamped,
    title: `Page ${clamped + 1}`,
    total_chapters: mangaDetail.page_count,
  };
  pageInput.value = String(clamped + 1);
  pageInput.max = String(Math.max(1, mangaDetail.page_count));
  pageTotalEl.textContent = `/ ${mangaDetail.page_count}`;
  syncTocSelect(clamped);
  syncPageScrubber(clamped);

  updateSpreadNavDisabled();
  const threadKey = `${mangaDetail.id}:${clamped}`;
  if (reloadThreads && loadedThreadKey !== threadKey) {
    loadedThreadKey = threadKey;
    await loadChapterGrammarThreads(mangaDetail.id, clamped);
    if (onPageChangedCb) onPageChangedCb();
  } else if (onPageChangedCb) {
    onPageChangedCb();
  }
}

function updateSpreadNavDisabled() {
  if (!mangaDetail) return;
  const layout = getLayoutMode();
  if (layout !== "spread") {
    btnPrev.disabled = currentPageIndex <= 0;
    btnNext.disabled = currentPageIndex >= mangaDetail.page_count - 1;
    return;
  }
  const sIdx = findSpreadIndexForPage(spreads, currentPageIndex);
  btnPrev.disabled = sIdx <= 0;
  btnNext.disabled = sIdx >= spreads.length - 1;
}

async function flushMangaProgress(extraDelta = false, { keepalive = false } = {}) {
  if (!mangaDetail || mangaView.hidden) return;
  const now = Date.now();
  let deltaSec = 0;
  if (
    extraDelta &&
    document.visibilityState === "visible" &&
    now - state.lastActivityMs < 65000
  ) {
    deltaSec = Math.min(60, Math.max(0, Math.round((now - lastProgressSavedMs) / 1000)));
  }
  lastProgressSavedMs = now;
  try {
    await api.saveProgress(
      mangaDetail.id,
      {
        chapter_index: currentPageIndex,
        scroll_ratio: 0,
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
  clearTimeout(progressTimer);
  progressTimer = setTimeout(() => flushMangaProgress(true), 700);
}

function getPageNaturalSize(pageObj, frameEl) {
  const img = frameEl?.querySelector("img");
  const natW = img?.naturalWidth || 0;
  const natH = img?.naturalHeight || 0;
  const w = pageObj?.width || natW || 1000;
  const h = pageObj?.height || natH || 1400;
  return { w, h };
}

function layoutVisiblePages() {
  if (!mangaDetail || mangaView.hidden) return;
  const frames = [...mangaStage.querySelectorAll(".manga-page-frame")];
  if (!frames.length) return;

  const isFs = isFullscreenActive();
  const layout = getLayoutMode();

  if (layout === "vertical") {
    const padX = isFs ? 16 : 32;
    const availW = Math.max(160, mangaViewport.clientWidth - padX);
    const colW = Math.min(availW, isFs ? 1280 : 960);
    for (const frame of frames) {
      const pIdx = parseInt(frame.dataset.pageIndex, 10);
      const pageObj = mangaDetail.pages[pIdx];
      const { w, h } = getPageNaturalSize(pageObj, frame);
      const dispW = Math.max(120, Math.floor(colW));
      const dispH = Math.max(120, Math.round(dispW * (h / w)));
      frame.style.width = `${dispW}px`;
      frame.style.height = `${dispH}px`;
    }
    return;
  }

  // 2-Page Spread mode (exact pixel preservation of natural aspect ratio)
  const padX = isFs ? 8 : 24;
  const padY = isFs ? 8 : 20;
  const availW = Math.max(120, mangaViewport.clientWidth - padX);
  const availH = Math.max(120, mangaViewport.clientHeight - padY);

  if (frames.length === 1) {
    const pIdx = parseInt(frames[0].dataset.pageIndex, 10);
    const { w, h } = getPageNaturalSize(mangaDetail.pages[pIdx], frames[0]);
    const scale = Math.min(availW / w, availH / h);
    frames[0].style.width = `${Math.max(1, Math.floor(w * scale))}px`;
    frames[0].style.height = `${Math.max(1, Math.floor(h * scale))}px`;
    return;
  }

  // Paired spread rendered left-to-right in DOM as [leftFrame (page N+1), rightFrame (page N)]
  const leftFrame = frames[0];
  const rightFrame = frames[1];
  const leftPage = mangaDetail.pages[parseInt(leftFrame.dataset.pageIndex, 10)];
  const rightPage = mangaDetail.pages[parseInt(rightFrame.dataset.pageIndex, 10)];
  const { w: lw, h: lh } = getPageNaturalSize(leftPage, leftFrame);
  const { w: rw, h: rh } = getPageNaturalSize(rightPage, rightFrame);

  const normLeftW = lw * (rh / lh);
  const totalW = normLeftW + rw;
  const refH = rh;
  const scale = Math.min(availW / totalW, availH / refH);

  const dispH = Math.max(1, Math.floor(refH * scale));
  const dispLeftW = Math.max(1, Math.floor(normLeftW * scale));
  const dispRightW = Math.max(1, Math.floor(rw * scale));

  leftFrame.style.width = `${dispLeftW}px`;
  leftFrame.style.height = `${dispH}px`;
  rightFrame.style.width = `${dispRightW}px`;
  rightFrame.style.height = `${dispH}px`;
}

export function refreshMangaTokenHighlights() {
  if (!textTokensEl) return;
  const active = state.troubleItems || [];
  const troubleSet = new Set(
    active.filter((i) => i.item_type === "word" && (i.memory_stage || 0) === 0).map((i) => i.item_key)
  );
  const stickySet = new Set(
    active.filter((i) => i.item_type === "word" && i.trouble_score >= 3.0).map((i) => i.item_key)
  );
  textTokensEl.querySelectorAll(".tok").forEach((el) => {
    const key = `${el.dataset.lemma || ""}|${el.dataset.reading || ""}`;
    el.classList.toggle("trouble-word", troubleSet.has(key));
    el.classList.toggle("sticky-word", stickySet.has(key));
  });
}

function applyInspectorTokenHighlights() {
  if (!textTokensEl) return;
  unwrapUserHighlights(textTokensEl);
  textTokensEl.classList.remove("has-note", ...HIGHLIGHT_COLORS.map((c) => `hl-${c}`));
  if (!mangaDetail || !activeInspector) return;

  const pageNotes = mangaNotesByPage.get(activeInspector.pageIndex) || [];
  const matching = pageNotes.filter(
    (n) => HIGHLIGHT_COLORS.includes(n.color) && n.sent_id === activeInspector.sentId
  );
  let fallbackColor = "";
  for (const note of matching) {
    const wrapped =
      note.quote_text &&
      applyQuoteHighlightToElements([textTokensEl], note.quote_text, {
        color: note.color,
        noteId: note.id,
      });
    if (!wrapped) {
      fallbackColor = note.color;
    }
  }
  if (fallbackColor) {
    textTokensEl.classList.add("has-note", `hl-${fallbackColor}`);
  }
}

export async function refreshMangaNoteMarks() {
  if (!mangaDetail) return;
  try {
    const res = await api.listNotes({ bookId: mangaDetail.id, limit: 500 });
    mangaNotesByPage.clear();
    const chronological = [...(res.notes || [])].reverse();
    for (const note of chronological) {
      const pIdx = Number(note.chapter_index);
      if (!mangaNotesByPage.has(pIdx)) mangaNotesByPage.set(pIdx, []);
      mangaNotesByPage.get(pIdx).push(note);
    }
    mangaStage.querySelectorAll(".manga-page-frame").forEach((frame) => {
      const pIdx = parseInt(frame.dataset.pageIndex, 10);
      if (!Number.isNaN(pIdx)) renderPageOcrLayer(frame, pIdx);
    });
    applyInspectorTokenHighlights();
  } catch {
    // ignore transient note mark refresh errors
  }
}

function renderTokensIntoBar(tokens, fallbackText) {
  textTokensEl.replaceChildren();
  if (!tokens || !tokens.length) {
    textTokensEl.textContent = fallbackText || "(No text recognized in selection — click Edit to enter text manually)";
    applyInspectorTokenHighlights();
    return;
  }

  for (const tok of tokens) {
    if (!tok.clickable) {
      textTokensEl.appendChild(document.createTextNode(tok.surface || ""));
      continue;
    }
    const span = document.createElement("span");
    span.className = "tok";
    span.dataset.lemma = tok.lemma || tok.surface || "";
    span.dataset.norm = tok.norm || tok.lemma || tok.surface || "";
    span.dataset.surface = tok.surface || "";
    span.dataset.reading = tok.reading || "";
    span.dataset.pos = tok.pos || "";
    span.textContent = tok.surface || "";
    textTokensEl.appendChild(span);
  }
  refreshMangaTokenHighlights();
  applyInspectorTokenHighlights();
}

export function closeMangaTextBar() {
  hideSelectionTooltip();
  closeLookup();
  textBar.hidden = true;
  textEditWrap.hidden = true;
  activeInspector = null;
  if (scrubberBar) scrubberBar.hidden = false;
  mangaStage.querySelectorAll(".manga-ocr-box.active").forEach((el) => el.classList.remove("active"));
}

function hideShotPopover() {
  if (shotPopover) shotPopover.hidden = true;
}

function getVisibleScreenshotPages() {
  if (!mangaDetail) return [];
  if (getLayoutMode() === "spread") {
    const idxs = [...mangaStage.querySelectorAll(".manga-page-frame")]
      .map((f) => parseInt(f.dataset.pageIndex, 10))
      .filter((n) => !Number.isNaN(n));
    if (idxs.length >= 1) return idxs.slice(0, 2);
  }
  return [currentPageIndex];
}

function parseDispositionFilename(headerVal, fallback) {
  if (!headerVal) return fallback;
  const utf8Match = headerVal.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(utf8Match[1].trim());
    } catch {
      // fallback below
    }
  }
  const asciiMatch = headerVal.match(/filename="([^"]+)"/i);
  return asciiMatch?.[1] || fallback;
}

async function captureMangaScreenshot(visualPages, { copyToClipboard = false } = {}) {
  if (!mangaDetail || !visualPages?.length) return;
  hideShotPopover();
  const label =
    visualPages.length === 2
      ? `Spread (p.${Math.min(...visualPages) + 1}–${Math.max(...visualPages) + 1})`
      : `Page ${visualPages[0] + 1}`;
  setOcrStatus(`Capturing ${label}…`);
  try {
    const url = `/api/manga/${mangaDetail.id}/screenshot?pages=${visualPages.join(",")}`;
    const resp = await fetch(url);
    if (!resp.ok) {
      let msg = `HTTP ${resp.status}`;
      try {
        const errData = await resp.json();
        if (errData?.detail) msg = String(errData.detail);
      } catch {
        // keep status
      }
      throw new Error(msg);
    }
    const blob = await resp.blob();
    const suffix =
      visualPages.length === 2
        ? `p${Math.min(...visualPages) + 1}-${Math.max(...visualPages) + 1}`
        : `p${visualPages[0] + 1}`;
    const filename = parseDispositionFilename(
      resp.headers.get("Content-Disposition"),
      `shiori_manga_${suffix}.png`
    );

    let clipboardFallback = false;
    if (copyToClipboard) {
      if (navigator.clipboard?.write && typeof ClipboardItem !== "undefined") {
        try {
          await navigator.clipboard.write([new ClipboardItem({ "image/png": blob })]);
          setOcrStatus(`Copied ${label} image to clipboard`, 2400);
          return;
        } catch {
          clipboardFallback = true;
        }
      } else {
        clipboardFallback = true;
      }
    }

    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(objUrl), 2500);
    setOcrStatus(
      clipboardFallback ? `Clipboard unavailable here — saved ${filename}` : `Saved screenshot: ${filename}`,
      2600
    );
  } catch (err) {
    setOcrStatus(`Screenshot failed: ${err.message}`, 3500);
  }
}

function populateShotPopover() {
  if (!shotActionsEl || !mangaDetail) return;
  shotActionsEl.replaceChildren();
  const visible = getVisibleScreenshotPages();
  if (!visible.length) return;

  const addRow = (title, pages, primarySave = false) => {
    const row = document.createElement("div");
    row.className = "manga-shot-row";

    const lbl = document.createElement("span");
    lbl.className = "manga-shot-row-label";
    lbl.textContent = title;

    const btns = document.createElement("div");
    btns.className = "manga-shot-row-btns";

    const saveBtn = document.createElement("button");
    saveBtn.type = "button";
    saveBtn.className = primarySave ? "btn btn-primary manga-shot-btn" : "btn btn-secondary manga-shot-btn";
    saveBtn.textContent = "⬇ Save";
    saveBtn.title = `Save ${title} (PNG)`;
    saveBtn.setAttribute("aria-label", `Save ${title}`);
    saveBtn.addEventListener("click", () =>
      captureMangaScreenshot(pages, { copyToClipboard: false })
    );

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "btn btn-secondary manga-shot-btn";
    copyBtn.textContent = "📋 Copy";
    copyBtn.title = `Copy ${title} to Clipboard`;
    copyBtn.setAttribute("aria-label", `Copy ${title}`);
    copyBtn.addEventListener("click", () =>
      captureMangaScreenshot(pages, { copyToClipboard: true })
    );

    btns.append(saveBtn, copyBtn);
    row.append(lbl, btns);
    shotActionsEl.appendChild(row);
  };

  if (visible.length === 2) {
    const [leftIdx, rightIdx] = visible;
    const low = Math.min(leftIdx, rightIdx) + 1;
    const high = Math.max(leftIdx, rightIdx) + 1;
    addRow(`Spread (p.${low}–${high})`, visible, true);
    addRow(`Right Page (p.${rightIdx + 1})`, [rightIdx], false);
    addRow(`Left Page (p.${leftIdx + 1})`, [leftIdx], false);
  } else {
    addRow(`Page ${visible[0] + 1}`, visible, true);
  }
}

async function openMangaTextInspector({ pageIndex, sentId, text, tokens, boxEl = null, label = "" }) {
  closeLookup();
  hideSelectionTooltip();
  await setActivePageContext(pageIndex, true);

  mangaStage.querySelectorAll(".manga-ocr-box.active").forEach((el) => el.classList.remove("active"));
  if (boxEl) boxEl.classList.add("active");

  activeInspector = {
    pageIndex,
    sentId: sentId || `p${pageIndex}_crop`,
    text: text || "",
    tokens: tokens || [],
  };

  textSourceEl.textContent = label || `Page ${pageIndex + 1}`;
  textTokensEl.dataset.sentId = activeInspector.sentId;
  textEditWrap.hidden = true;
  textEditInput.value = activeInspector.text;

  renderTokensIntoBar(activeInspector.tokens, activeInspector.text);
  const activeSentIds = new Set((state.grammarThreads || []).map((t) => t.sent_id));
  textTokensEl.classList.toggle("has-grammar", activeSentIds.has(activeInspector.sentId));

  if (scrubberBar) scrubberBar.hidden = true;
  textBar.hidden = false;
}

function renderPageOcrLayer(frameEl, pageIndex) {
  const layer = frameEl.querySelector(".manga-ocr-layer");
  if (!layer) return;
  layer.replaceChildren();

  const ocrData = ocrCache.get(pageIndex);
  if (!ocrData || !ocrData.ocr_available) return;

  const activeSentIds = new Set((state.grammarThreads || []).map((t) => t.sent_id));
  const noteBySent = new Map();
  for (const note of mangaNotesByPage.get(pageIndex) || []) {
    if (note.sent_id && HIGHLIGHT_COLORS.includes(note.color)) {
      noteBySent.set(note.sent_id, note);
    }
  }

  for (const blk of ocrData.blocks || []) {
    const pct = blk.box_pct;
    if (!pct || pct.length < 4) continue;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "manga-ocr-box";
    if (activeInspector && activeInspector.sentId === blk.block_id) {
      btn.classList.add("active");
    }
    if (activeSentIds.has(blk.block_id)) {
      btn.classList.add("has-grammar");
    }
    const matchedNote = noteBySent.get(blk.block_id);
    if (matchedNote) {
      btn.classList.add("has-note", `hl-${matchedNote.color}`);
      btn.dataset.noteId = String(matchedNote.id);
    }
    btn.dataset.sentId = blk.block_id;
    btn.dataset.pageIndex = String(pageIndex);
    btn.style.left = `${pct[0]}%`;
    btn.style.top = `${pct[1]}%`;
    btn.style.width = `${pct[2]}%`;
    btn.style.height = `${pct[3]}%`;
    btn.title = blk.text || "Speech bubble";
    btn.setAttribute("aria-label", blk.text || `Text block on page ${pageIndex + 1}`);

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      if (cropMode || e.shiftKey) return;
      openMangaTextInspector({
        pageIndex,
        sentId: blk.block_id,
        text: blk.text,
        tokens: blk.tokens || [],
        boxEl: btn,
        label: `Page ${pageIndex + 1} · Bubble`,
      });
    });

    layer.appendChild(btn);
  }
}

function isPageCurrentlyMounted(pageIndex) {
  return Boolean(mangaStage.querySelector(`.manga-page-frame[data-page-index="${pageIndex}"]`));
}

async function processOcrQueue() {
  if (ocrRunning) return;
  ocrRunning = true;
  try {
    while (ocrQueue.length > 0) {
      ocrQueue.sort((a, b) => (b.priority ? 1 : 0) - (a.priority ? 1 : 0));
      const job = ocrQueue.shift();
      if (!job || job.sessionNonce !== sessionNonce || !mangaDetail || job.bookId !== mangaDetail.id) {
        continue;
      }
      if (!job.force && ocrCache.has(job.pageIndex)) {
        continue;
      }
      const isVisibleNow = isPageCurrentlyMounted(job.pageIndex);
      if (isVisibleNow) {
        setOcrStatus(`Scanning text on Page ${job.pageIndex + 1}…`);
      }

      try {
        const res = await api.ocrMangaPage(job.bookId, job.pageIndex, job.force);
        if (job.sessionNonce !== sessionNonce) continue;
        ocrCache.set(job.pageIndex, res);

        if (isVisibleNow) {
          const frame = mangaStage.querySelector(`.manga-page-frame[data-page-index="${job.pageIndex}"]`);
          if (frame) renderPageOcrLayer(frame, job.pageIndex);

          if (!res.ocr_available) {
            setOcrStatus(res.error || "Manga OCR unavailable", 5000);
          } else if (job.force) {
            const count = (res.blocks || []).length;
            setOcrStatus(`Page ${job.pageIndex + 1}: ${count} text block(s) ready`, 2200);
          } else {
            setOcrStatus("");
          }
        }
      } catch (err) {
        if (job.sessionNonce === sessionNonce && isVisibleNow) {
          setOcrStatus(`OCR failed: ${err.message}`, 4000);
        }
      }
    }
  } finally {
    ocrRunning = false;
  }
}

function enqueueOcr(pageIndices, { priority = false, force = false } = {}) {
  if (!mangaDetail) return;
  if (!force && getOcrOverlayMode() === "off") return;

  const bookId = mangaDetail.id;
  // Drop stale non-priority background prefetch jobs when user navigates quickly
  if (priority) {
    ocrQueue = ocrQueue.filter((j) => j.priority || j.force);
  }

  for (const pIdx of pageIndices) {
    if (pIdx < 0 || pIdx >= mangaDetail.page_count) continue;
    if (!force && ocrCache.has(pIdx)) continue;
    const alreadyQueued = ocrQueue.find((j) => j.pageIndex === pIdx && j.bookId === bookId);
    if (alreadyQueued) {
      if (priority) alreadyQueued.priority = true;
      if (force) alreadyQueued.force = true;
      continue;
    }
    ocrQueue.push({
      bookId,
      sessionNonce,
      pageIndex: pIdx,
      force,
      priority,
    });
  }
  processOcrQueue();
}

function createPageFrame(pageObj, { eager = true } = {}) {
  const frame = document.createElement("div");
  frame.className = "manga-page-frame";
  frame.dataset.pageIndex = String(pageObj.page_index);

  const img = document.createElement("img");
  img.className = "manga-page-img";
  img.alt = `Page ${pageObj.page_index + 1}`;
  img.draggable = false;
  if (eager) {
    img.src = pageObj.image_url;
  } else {
    img.dataset.src = pageObj.image_url;
  }
  img.addEventListener("load", () => {
    if (!pageObj.width || !pageObj.height) {
      layoutVisiblePages();
    }
  });

  const layer = document.createElement("div");
  layer.className = "manga-ocr-layer";

  frame.append(img, layer);
  if (ocrCache.has(pageObj.page_index)) {
    renderPageOcrLayer(frame, pageObj.page_index);
  }
  return frame;
}

function disconnectVerticalObservers() {
  verticalLoadObserver?.disconnect();
  verticalLoadObserver = null;
  verticalTrackObserver?.disconnect();
  verticalTrackObserver = null;
}

function applyMangaTransform() {
  const group = mangaStage.querySelector(".manga-spread-group");
  if (!group) return;
  if (mangaZoom <= 1.01) {
    mangaZoom = 1;
    panX = 0;
    panY = 0;
    group.style.transform = "";
    mangaViewport.classList.remove("is-zoomed");
    return;
  }
  const baseW = group.offsetWidth || mangaViewport.clientWidth;
  const baseH = group.offsetHeight || mangaViewport.clientHeight;
  const maxPanX = Math.max(0, (baseW * (mangaZoom - 1)) / 2);
  const maxPanY = Math.max(0, (baseH * (mangaZoom - 1)) / 2);
  panX = Math.max(-maxPanX, Math.min(maxPanX, panX));
  panY = Math.max(-maxPanY, Math.min(maxPanY, panY));
  group.style.transform = `translate3d(${panX.toFixed(1)}px, ${panY.toFixed(1)}px, 0) scale(${mangaZoom.toFixed(3)})`;
  mangaViewport.classList.add("is-zoomed");
}

function resetMangaZoom() {
  mangaZoom = 1;
  panX = 0;
  panY = 0;
  applyMangaTransform();
}

function renderStage({ skipOcr = false } = {}) {
  if (!mangaDetail) return;
  disconnectVerticalObservers();
  resetMangaZoom();
  mangaStage.replaceChildren();

  const layout = getLayoutMode();
  mangaStage.classList.toggle("mode-spread", layout === "spread");
  mangaStage.classList.toggle("mode-single", layout === "single");
  mangaStage.classList.toggle("mode-vertical", layout === "vertical");
  mangaViewport.classList.toggle("mode-horizontal-manga", layout !== "vertical");
  applyToolbarLabels();

  if (layout === "vertical") {
    const frames = [];
    for (const pageObj of mangaDetail.pages) {
      const frame = createPageFrame(pageObj, {
        eager: Math.abs(pageObj.page_index - currentPageIndex) <= 3,
      });
      mangaStage.appendChild(frame);
      frames.push(frame);
    }

    layoutVisiblePages();

    verticalLoadObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const img = entry.target.querySelector("img[data-src]");
          if (img && img.dataset.src) {
            img.src = img.dataset.src;
            delete img.dataset.src;
          }
        }
      },
      { root: mangaViewport, rootMargin: "700px 0px" }
    );

    const visibleRatios = new Map();
    verticalTrackObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const pIdx = parseInt(entry.target.dataset.pageIndex, 10);
          visibleRatios.set(pIdx, entry.intersectionRatio);
        }
        if (suppressVerticalScrollTrack) return;

        let bestIdx = currentPageIndex;
        let bestRatio = -1;
        for (const [pIdx, ratio] of visibleRatios.entries()) {
          if (ratio > bestRatio) {
            bestRatio = ratio;
            bestIdx = pIdx;
          }
        }
        if (bestRatio > 0.05 && bestIdx !== currentPageIndex) {
          setActivePageContext(bestIdx, true);
          scheduleProgressSave();
          enqueueOcr([bestIdx], { priority: true });
          enqueueOcr([bestIdx + 1, bestIdx + 2], { priority: false });
        }
      },
      { root: mangaViewport, threshold: [0.1, 0.35, 0.6, 0.85] }
    );

    for (const f of frames) {
      verticalLoadObserver.observe(f);
      verticalTrackObserver.observe(f);
    }

    const targetFrame = frames[currentPageIndex] || frames[0];
    if (targetFrame) {
      suppressVerticalScrollTrack = true;
      requestAnimationFrame(() => {
        layoutVisiblePages();
        targetFrame.scrollIntoView({ block: "start", behavior: "auto" });
        setTimeout(() => {
          suppressVerticalScrollTrack = false;
        }, 180);
      });
    }

    if (!skipOcr) {
      enqueueOcr([currentPageIndex], { priority: true });
      enqueueOcr([currentPageIndex + 1, currentPageIndex + 2], { priority: false });
    }
    return;
  }

  if (layout === "single") {
    const pageObj = mangaDetail.pages[currentPageIndex] || mangaDetail.pages[0];
    if (pageObj) {
      const group = document.createElement("div");
      group.className = "manga-spread-group";
      group.appendChild(createPageFrame(pageObj, { eager: true }));
      mangaStage.appendChild(group);
      requestAnimationFrame(layoutVisiblePages);
      if (!skipOcr) {
        enqueueOcr([pageObj.page_index], { priority: true });
        enqueueOcr([pageObj.page_index + 1, pageObj.page_index + 2], { priority: false });
      }
    }
    return;
  }

  // 2-Page Spread mode (Japanese RTL: [page N+1 on LEFT][page N on RIGHT])
  const sIdx = findSpreadIndexForPage(spreads, currentPageIndex);
  const spreadPages = spreads[sIdx] || [];
  const spreadGroup = document.createElement("div");
  spreadGroup.className = "manga-spread-group";
  if (spreadPages.length === 2) {
    const [rightPage, leftPage] = spreadPages;
    spreadGroup.appendChild(createPageFrame(leftPage, { eager: true }));
    spreadGroup.appendChild(createPageFrame(rightPage, { eager: true }));
  } else if (spreadPages.length === 1) {
    spreadGroup.appendChild(createPageFrame(spreadPages[0], { eager: true }));
  }
  mangaStage.appendChild(spreadGroup);

  requestAnimationFrame(layoutVisiblePages);

  if (!skipOcr) {
    const visibleIdxs = spreadPages.map((p) => p.page_index);
    enqueueOcr(visibleIdxs, { priority: true });
    for (const offset of [1, 2]) {
      if (sIdx + offset < spreads.length) {
        enqueueOcr(
          spreads[sIdx + offset].map((p) => p.page_index),
          { priority: false }
        );
      }
    }
  }
}

export async function goToMangaPage(targetPageIndex, { syncRender = true, skipOcr = false } = {}) {
  if (!mangaDetail || !mangaDetail.page_count) return;
  const clamped = Math.max(0, Math.min(mangaDetail.page_count - 1, Number(targetPageIndex) || 0));
  hideShotPopover();
  resetMangaZoom();

  if (getLayoutMode() === "vertical") {
    closeMangaTextBar();
    await setActivePageContext(clamped, !skipOcr);
    scheduleProgressSave();
    const frame = mangaStage.querySelector(`.manga-page-frame[data-page-index="${clamped}"]`);
    if (frame) {
      suppressVerticalScrollTrack = true;
      frame.scrollIntoView({ block: "start", behavior: "auto" });
      const img = frame.querySelector("img[data-src]");
      if (img && img.dataset.src) {
        img.src = img.dataset.src;
        delete img.dataset.src;
      }
      setTimeout(() => {
        suppressVerticalScrollTrack = false;
      }, 180);
    } else if (syncRender) {
      renderStage({ skipOcr });
    }
    if (!skipOcr) {
      enqueueOcr([clamped], { priority: true });
      enqueueOcr([clamped + 1, clamped + 2], { priority: false });
    }
    return;
  }

  closeMangaTextBar();
  await setActivePageContext(clamped, !skipOcr);
  scheduleProgressSave();
  if (syncRender) renderStage({ skipOcr });
}

export function stepManga(delta) {
  if (!mangaDetail) return;
  if (getLayoutMode() !== "spread") {
    goToMangaPage(currentPageIndex + delta);
    return;
  }
  const sIdx = findSpreadIndexForPage(spreads, currentPageIndex);
  const nextSpreadIdx = sIdx + delta;
  if (nextSpreadIdx < 0 || nextSpreadIdx >= spreads.length) return;
  const targetPage = spreads[nextSpreadIdx][0].page_index;
  goToMangaPage(targetPage);
}

export async function openManga(bookId, startPage = null) {
  sessionNonce += 1;
  loadedThreadKey = "";
  ocrQueue = [];
  ocrCache.clear();
  mangaNotesByPage.clear();
  cropMode = false;
  closeLookup();
  hideSelectionTooltip();
  closeGrammarDrawer();
  hideShotPopover();
  closeMangaTextBar();
  document.documentElement.classList.remove("mobile-chrome-hidden");
  setOcrStatus("");

  const detail = await api.getManga(bookId);
  mangaDetail = detail;
  state.currentBook = detail;
  state.sessionStartedAt = new Date().toISOString();

  spreads = computeSpreads(detail.pages || [], getSpreadOffset());
  const resumePage =
    startPage != null ? Number(startPage) : Number(detail.progress?.chapter_index || 0);
  const clamped = Math.max(0, Math.min(Math.max(0, detail.page_count - 1), resumePage));

  mangaBookTitle.textContent = detail.title;

  const tocEntries = detail.toc || [];
  if (tocSelect) {
    tocSelect.replaceChildren();
    for (const entry of tocEntries) {
      const opt = document.createElement("option");
      opt.value = String(entry.page_index);
      opt.textContent = `${entry.title} (p.${entry.page_index + 1})`;
      tocSelect.appendChild(opt);
    }
    tocSelect.hidden = tocEntries.length === 0;
  }

  libraryView.hidden = true;
  readerView.hidden = true;
  mangaView.hidden = false;
  dockGrammarDrawer(mangaWorkspace);
  applyToolbarLabels();
  syncFullscreenUi();

  await setActivePageContext(clamped, true);
  await refreshMangaNoteMarks();
  renderStage();
  lastProgressSavedMs = Date.now();
  await flushMangaProgress(false);
}

export async function jumpToMangaHighlight({
  bookId,
  pageIndex = 0,
  sentId = null,
  noteId = null,
  quoteText = "",
}) {
  const targetPage = Math.max(0, Number(pageIndex) || 0);
  if (!mangaDetail || mangaDetail.id !== bookId || mangaView.hidden) {
    await openManga(bookId, targetPage);
  } else {
    await goToMangaPage(targetPage);
  }
  await refreshMangaNoteMarks();

  if (!sentId && !stripHighlightMarkup(quoteText).trim()) return;

  const frame = mangaStage.querySelector(`.manga-page-frame[data-page-index="${targetPage}"]`);
  if (sentId && /^p\d+_b\d+$/.test(sentId) && !ocrCache.has(targetPage)) {
    try {
      const ocrRes = await api.ocrMangaPage(bookId, targetPage, false);
      ocrCache.set(targetPage, ocrRes);
      if (frame) renderPageOcrLayer(frame, targetPage);
    } catch {
      // fallback to saved quote text below
    }
  }

  const ocrData = ocrCache.get(targetPage);
  const blk = sentId
    ? (ocrData?.blocks || []).find((b) => b.block_id === sentId)
    : null;
  const boxEl = sentId
    ? mangaStage.querySelector(
        `.manga-ocr-box[data-page-index="${targetPage}"][data-sent-id="${CSS.escape(sentId)}"]`
      )
    : null;

  if (blk) {
    await openMangaTextInspector({
      pageIndex: targetPage,
      sentId: blk.block_id,
      text: blk.text,
      tokens: blk.tokens || [],
      boxEl,
      label: `Page ${targetPage + 1} · Saved Highlight`,
    });
    if (boxEl) {
      boxEl.classList.remove("hl-jump-flash");
      void boxEl.offsetWidth;
      boxEl.classList.add("hl-jump-flash");
      setTimeout(() => boxEl.classList.remove("hl-jump-flash"), 1600);
    }
    return;
  }

  const cleanQuote = stripHighlightMarkup(quoteText).trim();
  if (cleanQuote) {
    let tokens = [];
    try {
      const tokRes = await api.tokenizeMangaText(cleanQuote);
      tokens = tokRes.tokens || [];
    } catch {
      tokens = [];
    }
    await openMangaTextInspector({
      pageIndex: targetPage,
      sentId: sentId || (noteId != null ? `note_${noteId}` : `p${targetPage}_crop`),
      text: cleanQuote,
      tokens,
      label: `Page ${targetPage + 1} · Saved Highlight`,
    });
  }
}

export async function closeMangaReader({ flush = true } = {}) {
  if (flush && mangaDetail && !mangaView.hidden) {
    await flushMangaProgress(true);
  }
  sessionNonce += 1;
  loadedThreadKey = "";
  ocrQueue = [];
  mangaNotesByPage.clear();
  cropMode = false;
  disconnectVerticalObservers();
  if (isFullscreenActive()) {
    await toggleMangaFullscreen(false);
  }
  resetMangaZoom();
  document.documentElement.classList.remove("mobile-chrome-hidden");
  hideShotPopover();
  closeMangaTextBar();
  closeGrammarDrawer();
  mangaStage.replaceChildren();
  mangaView.hidden = true;
  syncFullscreenUi();
}

function attachCropGestureHandler() {
  let dragState = null;

  mangaStage.addEventListener("pointerdown", (e) => {
    if (mangaView.hidden || !mangaDetail) return;
    if (e.button !== 0) return;
    if (!(cropMode || e.shiftKey)) return;

    const frame = e.target.closest?.(".manga-page-frame");
    if (!frame) return;

    e.preventDefault();
    e.stopPropagation();

    const rect = frame.getBoundingClientRect();
    const pageIndex = parseInt(frame.dataset.pageIndex, 10);
    try {
      frame.setPointerCapture?.(e.pointerId);
    } catch {
      // ignore unsupported pointer capture
    }
    const box = document.createElement("div");
    box.className = "manga-crop-rect";
    frame.appendChild(box);

    dragState = {
      frame,
      rect,
      pageIndex,
      startX: e.clientX,
      startY: e.clientY,
      box,
    };
  });

  window.addEventListener("pointercancel", () => {
    if (!dragState) return;
    dragState.box?.remove();
    dragState = null;
  });

  window.addEventListener("pointermove", (e) => {
    if (!dragState) return;
    const { rect, startX, startY, box } = dragState;
    const x1 = Math.max(rect.left, Math.min(rect.right, startX));
    const y1 = Math.max(rect.top, Math.min(rect.bottom, startY));
    const x2 = Math.max(rect.left, Math.min(rect.right, e.clientX));
    const y2 = Math.max(rect.top, Math.min(rect.bottom, e.clientY));

    const leftPx = Math.min(x1, x2) - rect.left;
    const topPx = Math.min(y1, y2) - rect.top;
    const widthPx = Math.abs(x2 - x1);
    const heightPx = Math.abs(y2 - y1);

    box.style.left = `${leftPx}px`;
    box.style.top = `${topPx}px`;
    box.style.width = `${widthPx}px`;
    box.style.height = `${heightPx}px`;
  });

  window.addEventListener("pointerup", async (e) => {
    if (!dragState) return;
    const { rect, startX, startY, box, pageIndex } = dragState;
    dragState = null;

    const x1 = Math.max(rect.left, Math.min(rect.right, startX));
    const y1 = Math.max(rect.top, Math.min(rect.bottom, startY));
    const x2 = Math.max(rect.left, Math.min(rect.right, e.clientX));
    const y2 = Math.max(rect.top, Math.min(rect.bottom, e.clientY));

    const leftPx = Math.min(x1, x2) - rect.left;
    const topPx = Math.min(y1, y2) - rect.top;
    const widthPx = Math.abs(x2 - x1);
    const heightPx = Math.abs(y2 - y1);

    box.remove();
    if (widthPx < 8 || heightPx < 8 || rect.width <= 0 || rect.height <= 0) {
      return;
    }

    didJustDragCrop = true;
    setTimeout(() => {
      didJustDragCrop = false;
    }, 150);

    const coords = {
      x: Math.max(0, Math.min(1, leftPx / rect.width)),
      y: Math.max(0, Math.min(1, topPx / rect.height)),
      w: Math.max(0.005, Math.min(1, widthPx / rect.width)),
      h: Math.max(0.005, Math.min(1, heightPx / rect.height)),
    };

    const callSession = sessionNonce;
    const callBookId = mangaDetail?.id;
    setOcrStatus(`Recognizing selected region on Page ${pageIndex + 1}…`);
    try {
      const res = await api.ocrMangaCrop(callBookId, pageIndex, coords);
      if (callSession !== sessionNonce) return;
      if (!res.ocr_available) {
        setOcrStatus(res.error || "Manga OCR unavailable", 5000);
        return;
      }
      setOcrStatus("");
      await openMangaTextInspector({
        pageIndex,
        sentId: res.block_id || `p${pageIndex}_crop`,
        text: res.text || "",
        tokens: res.tokens || [],
        label: `Page ${pageIndex + 1} · Crop OCR`,
      });
    } catch (err) {
      if (callSession === sessionNonce) {
        setOcrStatus(`Crop OCR failed: ${err.message}`, 4000);
      }
    }
  });
}

function attachMangaTouchGestures() {
  let touchMode = "none"; // "none" | "pinch" | "drag" | "swipe"
  let pinchStartDist = 0;
  let pinchStartZoom = 1;
  let pinchStartMidX = 0;
  let pinchStartMidY = 0;
  let pinchStartPanX = 0;
  let pinchStartPanY = 0;
  let dragStartX = 0;
  let dragStartY = 0;
  let dragStartPanX = 0;
  let dragStartPanY = 0;
  let dragStartTime = 0;
  let lastTapTime = 0;
  let lastTapX = 0;
  let lastTapY = 0;

  const armSuppressClick = (ms = 280) => {
    suppressNextMangaClick = true;
    clearTimeout(suppressClickTimer);
    suppressClickTimer = setTimeout(() => {
      suppressNextMangaClick = false;
    }, ms);
  };

  const getDist = (t1, t2) => Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
  const getMid = (t1, t2) => ({
    x: (t1.clientX + t2.clientX) / 2,
    y: (t1.clientY + t2.clientY) / 2,
  });

  mangaViewport.addEventListener(
    "touchstart",
    (e) => {
      if (mangaView.hidden || !mangaDetail || cropMode || getLayoutMode() === "vertical") {
        touchMode = "none";
        return;
      }
      if (e.target.closest?.(".manga-ocr-box, #manga-text-bar, #manga-shot-popover, #manga-ocr-status")) {
        touchMode = "none";
        return;
      }
      if (e.touches.length === 2) {
        e.preventDefault();
        const mid = getMid(e.touches[0], e.touches[1]);
        pinchStartDist = getDist(e.touches[0], e.touches[1]);
        pinchStartZoom = mangaZoom;
        pinchStartMidX = mid.x;
        pinchStartMidY = mid.y;
        pinchStartPanX = panX;
        pinchStartPanY = panY;
        touchMode = "pinch";
        armSuppressClick(350);
      } else if (e.touches.length === 1) {
        const t = e.touches[0];
        dragStartX = t.clientX;
        dragStartY = t.clientY;
        dragStartPanX = panX;
        dragStartPanY = panY;
        dragStartTime = Date.now();
        touchMode = mangaZoom > 1.05 ? "drag" : "swipe";
      }
    },
    { passive: false }
  );

  mangaViewport.addEventListener(
    "touchmove",
    (e) => {
      if (touchMode === "none") return;
      if (touchMode === "pinch" && e.touches.length >= 2) {
        e.preventDefault();
        const dist = getDist(e.touches[0], e.touches[1]);
        const mid = getMid(e.touches[0], e.touches[1]);
        mangaZoom = Math.max(
          1.0,
          Math.min(3.5, pinchStartZoom * (dist / Math.max(10, pinchStartDist)))
        );
        panX = pinchStartPanX + (mid.x - pinchStartMidX);
        panY = pinchStartPanY + (mid.y - pinchStartMidY);
        applyMangaTransform();
        armSuppressClick(350);
      } else if (touchMode === "drag" && e.touches.length === 1) {
        e.preventDefault();
        const t = e.touches[0];
        const dx = t.clientX - dragStartX;
        const dy = t.clientY - dragStartY;
        panX = dragStartPanX + dx;
        panY = dragStartPanY + dy;
        applyMangaTransform();
        if (Math.hypot(dx, dy) > 8) armSuppressClick(280);
      } else if (touchMode === "swipe" && e.touches.length === 1) {
        const t = e.touches[0];
        const dx = t.clientX - dragStartX;
        const dy = t.clientY - dragStartY;
        if (Math.abs(dx) > 14 && Math.abs(dx) > Math.abs(dy)) {
          e.preventDefault();
        }
      }
    },
    { passive: false }
  );

  const handleTouchEnd = (e) => {
    if (touchMode === "none") return;
    if (touchMode === "pinch") {
      if (e.touches.length === 1) {
        const t = e.touches[0];
        dragStartX = t.clientX;
        dragStartY = t.clientY;
        dragStartPanX = panX;
        dragStartPanY = panY;
        touchMode = mangaZoom > 1.05 ? "drag" : "none";
        return;
      }
      if (e.touches.length === 0) {
        touchMode = "none";
        armSuppressClick(280);
      }
      return;
    }

    if (e.touches.length > 0) return;
    const prevMode = touchMode;
    touchMode = "none";
    const t = e.changedTouches?.[0];
    if (!t) return;

    const dx = t.clientX - dragStartX;
    const dy = t.clientY - dragStartY;
    const dt = Date.now() - dragStartTime;
    const now = Date.now();

    if (prevMode === "swipe" && Math.abs(dx) >= 48 && Math.abs(dx) > Math.abs(dy) * 1.4 && dt < 600) {
      armSuppressClick(320);
      lastTapTime = 0;
      stepManga(dx > 0 ? 1 : -1);
      return;
    }

    if (Math.hypot(dx, dy) < 12 && dt < 280) {
      const isDoubleTap =
        now - lastTapTime < 280 && Math.hypot(t.clientX - lastTapX, t.clientY - lastTapY) < 32;
      if (isDoubleTap) {
        lastTapTime = 0;
        armSuppressClick(340);
        if (mangaZoom > 1.05) {
          resetMangaZoom();
        } else {
          const vpRect = mangaViewport.getBoundingClientRect();
          mangaZoom = 2.1;
          panX = (vpRect.left + vpRect.width / 2 - t.clientX) * 1.1;
          panY = (vpRect.top + vpRect.height / 2 - t.clientY) * 1.1;
          applyMangaTransform();
        }
        return;
      }
      lastTapTime = now;
      lastTapX = t.clientX;
      lastTapY = t.clientY;
      return;
    }

    if (prevMode === "drag" && Math.hypot(dx, dy) > 8) {
      armSuppressClick(280);
    }
  };

  mangaViewport.addEventListener("touchend", handleTouchEnd);
  mangaViewport.addEventListener("touchcancel", () => {
    touchMode = "none";
  });
}

export function initManga({
  onBackToLibrary,
  onSaveNote,
  onOpenTrouble,
  onOpenSettings,
  onOpenNotes,
  onPageChanged,
  onHighlightCreated,
}) {
  onBackLibraryCb = onBackToLibrary;
  onSaveNoteCb = onSaveNote;
  onOpenTroubleCb = onOpenTrouble;
  onOpenSettingsCb = onOpenSettings;
  onOpenNotesCb = onOpenNotes;
  onPageChangedCb = onPageChanged;
  onHighlightCreatedCb = onHighlightCreated;

  const ro = new ResizeObserver(() => {
    if (!mangaView.hidden) {
      requestAnimationFrame(layoutVisiblePages);
    }
  });
  ro.observe(mangaViewport);
  window.addEventListener("resize", () => {
    if (!mangaView.hidden) {
      resetMangaZoom();
      applyToolbarLabels();
      layoutVisiblePages();
    }
  });
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement) pseudoFullscreen = false;
    syncFullscreenUi();
    if (!mangaView.hidden) {
      requestAnimationFrame(layoutVisiblePages);
    }
  });

  btnBack.addEventListener("click", async () => {
    await closeMangaReader({ flush: true });
    if (onBackLibraryCb) onBackLibraryCb();
  });

  btnNext.addEventListener("click", () => stepManga(1));
  btnPrev.addEventListener("click", () => stepManga(-1));

  pageInput.addEventListener("change", () => {
    if (!mangaDetail) return;
    const val = parseInt(pageInput.value, 10);
    if (!Number.isNaN(val)) {
      goToMangaPage(val - 1);
    } else {
      pageInput.value = String(currentPageIndex + 1);
    }
  });

  if (scrubberBar) {
    for (const evt of ["pointerdown", "touchstart", "click"]) {
      scrubberBar.addEventListener(evt, (e) => e.stopPropagation());
    }
  }

  pageSlider?.addEventListener("input", () => {
    if (!mangaDetail) return;
    const val = parseInt(pageSlider.value, 10);
    if (Number.isNaN(val)) return;
    isScrubbing = true;
    const clamped = Math.max(0, Math.min(mangaDetail.page_count - 1, val - 1));
    if (scrubberLabel) scrubberLabel.textContent = `${clamped + 1} / ${mangaDetail.page_count}`;
    pageInput.value = String(clamped + 1);
    clearTimeout(scrubTimer);
    scrubTimer = setTimeout(() => {
      goToMangaPage(clamped, { syncRender: true, skipOcr: true });
    }, 50);
  });

  pageSlider?.addEventListener("change", () => {
    if (!mangaDetail) return;
    clearTimeout(scrubTimer);
    isScrubbing = false;
    const val = parseInt(pageSlider.value, 10);
    if (Number.isNaN(val)) return;
    goToMangaPage(val - 1, { syncRender: true, skipOcr: false });
  });

  tocSelect?.addEventListener("change", () => {
    if (!mangaDetail) return;
    const target = parseInt(tocSelect.value, 10);
    if (!Number.isNaN(target)) {
      goToMangaPage(target);
      tocSelect.blur();
    }
  });

  btnLayout.addEventListener("click", async () => {
    const cur = getLayoutMode();
    const nextLayout = MANGA_LAYOUTS[(MANGA_LAYOUTS.indexOf(cur) + 1) % MANGA_LAYOUTS.length];
    if (isMobileViewport()) {
      localStorage.setItem(MOBILE_LAYOUT_STORAGE_KEY, nextLayout);
      applyToolbarLabels();
    } else {
      await persistMangaPref({ manga_layout: nextLayout });
    }
    renderStage();
    updateSpreadNavDisabled();
  });

  btnOffset.addEventListener("click", async () => {
    const nextOffset = getSpreadOffset() === 1 ? "0" : "1";
    await persistMangaPref({ manga_spread_offset: nextOffset });
    if (mangaDetail) {
      spreads = computeSpreads(mangaDetail.pages || [], parseInt(nextOffset, 10));
      renderStage();
      updateSpreadNavDisabled();
    }
  });

  btnOcrMode.addEventListener("click", async () => {
    const cur = getOcrOverlayMode();
    const next = OCR_MODES[(OCR_MODES.indexOf(cur) + 1) % OCR_MODES.length];
    await persistMangaPref({ manga_ocr_overlay: next });
    if (next !== "off" && mangaDetail) {
      const sIdx = findSpreadIndexForPage(spreads, currentPageIndex);
      const visiblePages =
        getLayoutMode() === "spread" ? (spreads[sIdx] || []).map((p) => p.page_index) : [currentPageIndex];
      enqueueOcr(visiblePages, { priority: true });
    }
  });

  btnHideMarks?.addEventListener("click", () => {
    const nextHidden = String(state.settings.hide_annotations || "false") !== "true";
    if (nextHidden) {
      closeMangaTextBar();
      closeGrammarDrawer();
      closeLookup();
      hideSelectionTooltip();
    }
    persistMangaPref({ hide_annotations: nextHidden ? "true" : "false" });
  });

  btnScanOcr.addEventListener("click", async () => {
    if (!mangaDetail) return;
    if (getOcrOverlayMode() === "off") {
      await persistMangaPref({ manga_ocr_overlay: "hover" });
    }
    const visibleFrames = [...mangaStage.querySelectorAll(".manga-page-frame")];
    const indices =
      getLayoutMode() === "spread"
        ? visibleFrames.map((f) => parseInt(f.dataset.pageIndex, 10))
        : [currentPageIndex];
    enqueueOcr(indices, { priority: true, force: true });
  });

  btnCrop.addEventListener("click", () => {
    hideShotPopover();
    cropMode = !cropMode;
    applyToolbarLabels();
    if (cropMode) {
      setOcrStatus("Drag a rectangle over any manga text (Esc to cancel)", 3000);
    } else {
      setOcrStatus("");
    }
  });

  btnScreenshot?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!shotPopover) return;
    if (!shotPopover.hidden) {
      hideShotPopover();
      return;
    }
    populateShotPopover();
    shotPopover.hidden = false;
    const rect = btnScreenshot.getBoundingClientRect();
    const popW = shotPopover.offsetWidth || 245;
    const popH = shotPopover.offsetHeight || 160;
    const left = Math.min(
      Math.max(12, rect.right - popW),
      Math.max(12, window.innerWidth - popW - 12)
    );
    const top = Math.min(
      Math.max(12, rect.bottom + 8),
      Math.max(12, window.innerHeight - popH - 12)
    );
    shotPopover.style.left = `${Math.round(left)}px`;
    shotPopover.style.top = `${Math.round(top)}px`;
    shotPopover.style.right = "auto";
  });

  document.addEventListener("pointerdown", (e) => {
    if (
      shotPopover &&
      !shotPopover.hidden &&
      !shotPopover.contains(e.target) &&
      !btnScreenshot?.contains(e.target)
    ) {
      hideShotPopover();
    }
  });

  btnTrouble.addEventListener("click", () => {
    if (onOpenTroubleCb) onOpenTroubleCb();
  });
  btnNotes.addEventListener("click", () => {
    if (onOpenNotesCb) onOpenNotesCb();
  });
  btnGrammar.addEventListener("click", () => toggleGrammarDrawer());
  btnSettings.addEventListener("click", () => {
    if (onOpenSettingsCb) onOpenSettingsCb();
  });
  btnFullscreen.addEventListener("click", () => toggleMangaFullscreen());

  // Click / tap navigation in horizontal modes (Left = Next RTL, Right = Previous RTL, Center = Mobile Chrome Toggle)
  mangaViewport.addEventListener("click", (e) => {
    if (mangaView.hidden || !mangaDetail) return;
    if (getLayoutMode() === "vertical") return;
    if (cropMode || e.shiftKey || didJustDragCrop || suppressNextMangaClick) return;
    if (mangaZoom > 1.05) return;
    if (e.target.closest?.(".manga-ocr-box, #manga-text-bar, #manga-ocr-status, #manga-shot-popover")) return;

    const rect = mangaViewport.getBoundingClientRect();
    const relX = (e.clientX - rect.left) / Math.max(1, rect.width);
    if (relX < 0.3) {
      stepManga(1);
    } else if (relX > 0.7) {
      stepManga(-1);
    } else if (isMobileViewport() || window.matchMedia?.("(pointer: coarse)")?.matches) {
      document.documentElement.classList.toggle("mobile-chrome-hidden");
      requestAnimationFrame(layoutVisiblePages);
    }
  });

  // Token clicks inside the text inspector bar
  textTokensEl.addEventListener("click", (e) => {
    state.lastActivityMs = Date.now();
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed && sel.toString().trim().length >= 1) return;
    const tokEl = e.target.closest?.(".tok");
    if (tokEl) {
      hideSelectionTooltip();
      openWordLookup(tokEl);
    }
  });

  btnBarClose.addEventListener("click", closeMangaTextBar);

  btnBarEdit.addEventListener("click", () => {
    textEditWrap.hidden = !textEditWrap.hidden;
    if (!textEditWrap.hidden) {
      textEditInput.value = activeInspector?.text || extractCleanText(textTokensEl);
      textEditInput.focus();
      textEditInput.select();
    }
  });

  async function applyEditedText() {
    const clean = textEditInput.value.trim();
    if (!clean || !activeInspector) return;
    try {
      const res = await api.tokenizeMangaText(clean);
      activeInspector.text = res.text || clean;
      activeInspector.tokens = res.tokens || [];
      renderTokensIntoBar(activeInspector.tokens, activeInspector.text);
      textEditWrap.hidden = true;
    } catch (err) {
      setOcrStatus(`Tokenize error: ${err.message}`, 3000);
    }
  }

  btnEditApply.addEventListener("click", applyEditedText);
  textEditInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      applyEditedText();
    }
  });

  btnBarTranslate?.addEventListener("click", () => {
    if (!activeInspector) return;
    closeLookup();
    hideSelectionTooltip();
    openGrammarForSentence({
      sentId: activeInspector.sentId,
      sentenceText: activeInspector.text,
      targetText: activeInspector.text,
      autoAsk: true,
      mode: "translate",
    });
  });

  btnBarChat.addEventListener("click", () => {
    if (!activeInspector) return;
    closeLookup();
    hideSelectionTooltip();
    openGrammarForSentence({
      sentId: activeInspector.sentId,
      sentenceText: activeInspector.text,
      targetText: "",
      autoAsk: false,
    });
  });

  btnBarGrammar.addEventListener("click", () => {
    if (!activeInspector) return;
    closeLookup();
    hideSelectionTooltip();
    openGrammarForSentence({
      sentId: activeInspector.sentId,
      sentenceText: activeInspector.text,
      targetText: "",
      autoAsk: true,
    });
  });

  btnBarSave.addEventListener("click", () => {
    if (!activeInspector || !mangaDetail || !onSaveNoteCb) return;
    closeLookup();
    hideSelectionTooltip();
    onSaveNoteCb({
      bookId: mangaDetail.id,
      chapterIndex: activeInspector.pageIndex,
      sentId: activeInspector.sentId,
      quoteText: activeInspector.text,
      category: "quote",
    });
  });

  textBar.querySelectorAll(".manga-hl-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!activeInspector || !mangaDetail) return;
      const color = btn.dataset.color || "yellow";
      const existingIds = (mangaNotesByPage.get(activeInspector.pageIndex) || [])
        .filter((n) => n.sent_id === activeInspector.sentId)
        .map((n) => n.id);
      try {
        await api.createNote({
          book_id: mangaDetail.id,
          chapter_index: activeInspector.pageIndex,
          sent_id: activeInspector.sentId,
          quote_text: activeInspector.text,
          note_text: "",
          category: "quote",
          color,
          replace_existing: true,
          target_note_ids: existingIds,
        });
        await refreshMangaNoteMarks();
        if (onHighlightCreatedCb) onHighlightCreatedCb();
      } catch (err) {
        setOcrStatus(`Highlight failed: ${err.message}`, 3000);
      }
    });
  });

  btnBarUnhighlight?.addEventListener("click", async () => {
    if (!activeInspector || !mangaDetail) return;
    const existingIds = (mangaNotesByPage.get(activeInspector.pageIndex) || [])
      .filter((n) => n.sent_id === activeInspector.sentId)
      .map((n) => n.id);
    try {
      await api.unhighlightNotes({
        book_id: mangaDetail.id,
        chapter_index: activeInspector.pageIndex,
        sent_id: activeInspector.sentId,
        quote_text: activeInspector.text,
        note_ids: existingIds,
      });
      await refreshMangaNoteMarks();
      if (onHighlightCreatedCb) onHighlightCreatedCb();
    } catch (err) {
      setOcrStatus(`Unhighlight failed: ${err.message}`, 3000);
    }
  });

  attachCropGestureHandler();
  attachMangaTouchGestures();

  document.addEventListener("keydown", (e) => {
    if (mangaView.hidden) return;
    if (
      !document.getElementById("quiz-modal").hidden ||
      !document.getElementById("trouble-modal").hidden ||
      !document.getElementById("notes-modal").hidden ||
      !document.getElementById("settings-modal").hidden
    ) {
      return;
    }

    if (e.key === "Escape") {
      if (shotPopover && !shotPopover.hidden) {
        hideShotPopover();
        return;
      }
      if (!textBar.hidden) {
        closeMangaTextBar();
        return;
      }
      if (cropMode) {
        cropMode = false;
        applyToolbarLabels();
        setOcrStatus("");
        return;
      }
      if (pseudoFullscreen && !document.fullscreenElement) {
        toggleMangaFullscreen(false);
      }
      return;
    }

    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

    if (e.key === "ArrowLeft" || e.key === " " || e.key === "PageDown") {
      e.preventDefault();
      stepManga(1);
    } else if (e.key === "ArrowRight" || e.key === "PageUp") {
      e.preventDefault();
      stepManga(-1);
    } else if (e.key === "f" || e.key === "F") {
      e.preventDefault();
      toggleMangaFullscreen();
    } else if (e.key === "v" || e.key === "V") {
      e.preventDefault();
      btnLayout.click();
    } else if (e.key === "s" || e.key === "S") {
      e.preventDefault();
      if (getLayoutMode() === "spread") btnOffset.click();
    } else if (e.key === "c" || e.key === "C") {
      e.preventDefault();
      btnCrop.click();
    } else if (e.key === "h" || e.key === "H") {
      e.preventDefault();
      btnHideMarks?.click();
    } else if (e.key === "p" || e.key === "P") {
      e.preventDefault();
      const pages = getVisibleScreenshotPages();
      if (pages.length) captureMangaScreenshot(pages, { copyToClipboard: false });
    }
  });

  window.addEventListener("beforeunload", () => {
    flushMangaProgress(true, { keepalive: true });
  });
  window.addEventListener("pagehide", () => {
    flushMangaProgress(true, { keepalive: true });
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      flushMangaProgress(true, { keepalive: true });
    }
  });
}
