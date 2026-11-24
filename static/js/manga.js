/** Manga reader view: RTL 2-page spreads, 1-page vertical scroll, exact aspect ratios, fullscreen, and CPU OCR overlays. */

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
import { extractCleanText, state } from "./state.js";

const libraryView = document.getElementById("library-view");
const readerView = document.getElementById("reader-view");
const mangaView = document.getElementById("manga-view");
const mangaWorkspace = document.getElementById("manga-workspace");
const mangaViewport = document.getElementById("manga-viewport");
const mangaStage = document.getElementById("manga-stage");
const mangaBookTitle = document.getElementById("manga-book-title");
const pageInput = document.getElementById("manga-page-input");
const pageTotalEl = document.getElementById("manga-page-total");

const btnBack = document.getElementById("btn-manga-back");
const btnPrev = document.getElementById("btn-manga-prev");
const btnNext = document.getElementById("btn-manga-next");
const btnLayout = document.getElementById("btn-manga-layout");
const btnOffset = document.getElementById("btn-manga-offset");
const btnOcrMode = document.getElementById("btn-manga-ocr-mode");
const btnScanOcr = document.getElementById("btn-manga-scan-ocr");
const btnCrop = document.getElementById("btn-manga-crop");
const btnTrouble = document.getElementById("btn-manga-trouble");
const btnNotes = document.getElementById("btn-manga-notes");
const btnGrammar = document.getElementById("btn-manga-grammar");
const btnSettings = document.getElementById("btn-manga-settings");
const btnFullscreen = document.getElementById("btn-manga-fullscreen");
const btnExitFs = document.getElementById("btn-manga-exit-fs");

const ocrStatusEl = document.getElementById("manga-ocr-status");
const textBar = document.getElementById("manga-text-bar");
const textSourceEl = document.getElementById("manga-text-source");
const textTokensEl = document.getElementById("manga-text-tokens");
const textEditWrap = document.getElementById("manga-text-edit-wrap");
const textEditInput = document.getElementById("manga-text-edit-input");
const btnEditApply = document.getElementById("btn-manga-edit-apply");
const btnBarChat = document.getElementById("btn-manga-bar-chat");
const btnBarGrammar = document.getElementById("btn-manga-bar-grammar");
const btnBarSave = document.getElementById("btn-manga-bar-save");
const btnBarEdit = document.getElementById("btn-manga-bar-edit");
const btnBarClose = document.getElementById("btn-manga-bar-close");

let mangaDetail = null;
let spreads = [];
let currentPageIndex = 0;
let sessionNonce = 0;
let loadedThreadKey = "";

const ocrCache = new Map(); // pageIndex -> ocr result
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

let onBackLibraryCb = null;
let onSaveNoteCb = null;
let onOpenTroubleCb = null;
let onOpenSettingsCb = null;
let onOpenNotesCb = null;
let onPageChangedCb = null;

const OCR_MODES = ["hover", "always", "off"];
const OCR_MODE_LABELS = {
  hover: "OCR: Hover",
  always: "OCR: Always",
  off: "OCR: Off",
};

function getLayoutMode() {
  return state.settings.manga_layout === "vertical" ? "vertical" : "spread";
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
  if (btnExitFs) btnExitFs.hidden = !active;
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

  btnLayout.textContent = layout === "vertical" ? "1-Page Vertical" : "2-Page Spread";
  btnOffset.textContent = offset === 1 ? "Cover: Solo" : "Cover: Paired";
  btnOffset.hidden = layout === "vertical";
  btnOcrMode.textContent = OCR_MODE_LABELS[ocrMode];
  mangaStage.dataset.ocrOverlay = ocrMode;
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

async function setActivePageContext(pageIndex, reloadThreads = true) {
  if (!mangaDetail) return;
  const clamped = Math.max(0, Math.min(mangaDetail.page_count - 1, Number(pageIndex) || 0));
  currentPageIndex = clamped;

  state.currentChapter = {
    chapter_index: clamped,
    title: `Page ${clamped + 1}`,
    total_chapters: mangaDetail.page_count,
  };
  pageInput.value = String(clamped + 1);
  pageInput.max = String(Math.max(1, mangaDetail.page_count));
  pageTotalEl.textContent = `/ ${mangaDetail.page_count}`;

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
  if (layout === "vertical") {
    btnPrev.disabled = currentPageIndex <= 0;
    btnNext.disabled = currentPageIndex >= mangaDetail.page_count - 1;
    return;
  }
  const sIdx = findSpreadIndexForPage(spreads, currentPageIndex);
  btnPrev.disabled = sIdx <= 0;
  btnNext.disabled = sIdx >= spreads.length - 1;
}

async function flushMangaProgress(extraDelta = false) {
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
    await api.saveProgress(mangaDetail.id, {
      chapter_index: currentPageIndex,
      scroll_ratio: 0,
      reading_seconds_delta: deltaSec,
    });
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

function renderTokensIntoBar(tokens, fallbackText) {
  textTokensEl.replaceChildren();
  if (!tokens || !tokens.length) {
    textTokensEl.textContent = fallbackText || "(No text recognized in selection — click Edit to enter text manually)";
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
}

export function closeMangaTextBar() {
  hideSelectionTooltip();
  closeLookup();
  textBar.hidden = true;
  textEditWrap.hidden = true;
  activeInspector = null;
  mangaStage.querySelectorAll(".manga-ocr-box.active").forEach((el) => el.classList.remove("active"));
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

  textBar.hidden = false;
}

function renderPageOcrLayer(frameEl, pageIndex) {
  const layer = frameEl.querySelector(".manga-ocr-layer");
  if (!layer) return;
  layer.replaceChildren();

  const ocrData = ocrCache.get(pageIndex);
  if (!ocrData || !ocrData.ocr_available) return;

  const activeSentIds = new Set((state.grammarThreads || []).map((t) => t.sent_id));
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

function renderStage() {
  if (!mangaDetail) return;
  disconnectVerticalObservers();
  mangaStage.replaceChildren();

  const layout = getLayoutMode();
  mangaStage.classList.toggle("mode-spread", layout === "spread");
  mangaStage.classList.toggle("mode-vertical", layout === "vertical");
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
          enqueueOcr([bestIdx + 1], { priority: false });
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

    enqueueOcr([currentPageIndex], { priority: true });
    enqueueOcr([currentPageIndex + 1], { priority: false });
    return;
  }

  // 2-Page Spread mode (Japanese RTL: [page N+1 on LEFT][page N on RIGHT])
  const sIdx = findSpreadIndexForPage(spreads, currentPageIndex);
  const spreadPages = spreads[sIdx] || [];
  if (spreadPages.length === 2) {
    const [rightPage, leftPage] = spreadPages;
    mangaStage.appendChild(createPageFrame(leftPage, { eager: true }));
    mangaStage.appendChild(createPageFrame(rightPage, { eager: true }));
  } else if (spreadPages.length === 1) {
    mangaStage.appendChild(createPageFrame(spreadPages[0], { eager: true }));
  }

  requestAnimationFrame(layoutVisiblePages);

  const visibleIdxs = spreadPages.map((p) => p.page_index);
  enqueueOcr(visibleIdxs, { priority: true });
  if (sIdx + 1 < spreads.length) {
    enqueueOcr(
      spreads[sIdx + 1].map((p) => p.page_index),
      { priority: false }
    );
  }
}

export async function goToMangaPage(targetPageIndex, { syncRender = true } = {}) {
  if (!mangaDetail || !mangaDetail.page_count) return;
  const clamped = Math.max(0, Math.min(mangaDetail.page_count - 1, Number(targetPageIndex) || 0));

  if (getLayoutMode() === "vertical") {
    closeMangaTextBar();
    await setActivePageContext(clamped, true);
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
      renderStage();
    }
    enqueueOcr([clamped], { priority: true });
    enqueueOcr([clamped + 1], { priority: false });
    return;
  }

  closeMangaTextBar();
  await setActivePageContext(clamped, true);
  scheduleProgressSave();
  if (syncRender) renderStage();
}

export function stepManga(delta) {
  if (!mangaDetail) return;
  if (getLayoutMode() === "vertical") {
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
  cropMode = false;
  closeLookup();
  hideSelectionTooltip();
  closeGrammarDrawer();
  closeMangaTextBar();
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

  libraryView.hidden = true;
  readerView.hidden = true;
  mangaView.hidden = false;
  dockGrammarDrawer(mangaWorkspace);
  applyToolbarLabels();
  syncFullscreenUi();

  await setActivePageContext(clamped, true);
  renderStage();
  lastProgressSavedMs = Date.now();
  await flushMangaProgress(false);
}

export async function closeMangaReader({ flush = true } = {}) {
  if (flush && mangaDetail && !mangaView.hidden) {
    await flushMangaProgress(true);
  }
  sessionNonce += 1;
  loadedThreadKey = "";
  ocrQueue = [];
  cropMode = false;
  disconnectVerticalObservers();
  if (isFullscreenActive()) {
    await toggleMangaFullscreen(false);
  }
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

export function initManga({
  onBackToLibrary,
  onSaveNote,
  onOpenTrouble,
  onOpenSettings,
  onOpenNotes,
  onPageChanged,
}) {
  onBackLibraryCb = onBackToLibrary;
  onSaveNoteCb = onSaveNote;
  onOpenTroubleCb = onOpenTrouble;
  onOpenSettingsCb = onOpenSettings;
  onOpenNotesCb = onOpenNotes;
  onPageChangedCb = onPageChanged;

  const ro = new ResizeObserver(() => {
    if (!mangaView.hidden) {
      requestAnimationFrame(layoutVisiblePages);
    }
  });
  ro.observe(mangaViewport);
  window.addEventListener("resize", () => {
    if (!mangaView.hidden) layoutVisiblePages();
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

  btnLayout.addEventListener("click", async () => {
    const nextLayout = getLayoutMode() === "vertical" ? "spread" : "vertical";
    await persistMangaPref({ manga_layout: nextLayout });
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
    cropMode = !cropMode;
    applyToolbarLabels();
    if (cropMode) {
      setOcrStatus("Drag a rectangle over any manga text (Esc to cancel)", 3000);
    } else {
      setOcrStatus("");
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
  btnExitFs.addEventListener("click", () => toggleMangaFullscreen(false));

  // Click navigation in 2-page spread mode (Left = Next RTL, Right = Previous RTL)
  mangaViewport.addEventListener("click", (e) => {
    if (mangaView.hidden || !mangaDetail) return;
    if (getLayoutMode() !== "spread") return;
    if (cropMode || e.shiftKey || didJustDragCrop) return;
    if (e.target.closest?.(".manga-ocr-box, #manga-text-bar, #manga-ocr-status, #btn-manga-exit-fs")) return;

    const rect = mangaViewport.getBoundingClientRect();
    const relX = (e.clientX - rect.left) / Math.max(1, rect.width);
    if (relX < 0.35) {
      stepManga(1);
    } else if (relX > 0.65) {
      stepManga(-1);
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

  attachCropGestureHandler();

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
    }
  });

  window.addEventListener("beforeunload", () => {
    flushMangaProgress(true);
  });
}
