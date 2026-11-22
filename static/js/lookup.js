/** Floating 2-step dictionary lookup popover (Monolingual J-J first -> optional English). */

import { api } from "./api.js";
import { extractCleanText, state } from "./state.js";

const popover = document.getElementById("lookup-popover");
const lemmaEl = document.getElementById("lookup-lemma");
const readingEl = document.getElementById("lookup-reading");
const surfaceNoteEl = document.getElementById("lookup-surface-note");
const posEl = document.getElementById("lookup-pos");
const btnRegen = document.getElementById("btn-lookup-regen");
const btnClose = document.getElementById("btn-lookup-close");

const jjLoading = document.getElementById("lookup-jj-loading");
const jjError = document.getElementById("lookup-jj-error");
const jjBody = document.getElementById("lookup-jj-body");
const jjDef = document.getElementById("lookup-jj-def");
const jjContext = document.getElementById("lookup-jj-context");
const jjExamples = document.getElementById("lookup-jj-examples");
const jjMeta = document.getElementById("lookup-jj-meta");

const btnRevealEn = document.getElementById("btn-reveal-en");
const enResults = document.getElementById("lookup-en-results");
const btnAskGrammar = document.getElementById("btn-lookup-ask-grammar");

let currentLookup = null;
let activeTokenEl = null;
let lookupNonce = 0;
let onAskGrammarCb = null;

function clearActiveToken() {
  if (activeTokenEl) {
    activeTokenEl.classList.remove("active");
    activeTokenEl = null;
  }
}

export function closeLookup() {
  popover.hidden = true;
  currentLookup = null;
  clearActiveToken();
}

function positionPopover(anchorRect) {
  popover.hidden = false;
  const pad = 12;
  const popRect = popover.getBoundingClientRect();

  let left = anchorRect.left;
  if (left + popRect.width > window.innerWidth - pad) {
    left = window.innerWidth - popRect.width - pad;
  }
  if (left < pad) left = pad;

  let top = anchorRect.bottom + 10;
  if (top + popRect.height > window.innerHeight - pad) {
    const above = anchorRect.top - popRect.height - 10;
    if (above >= pad) {
      top = above;
    } else {
      top = Math.max(pad, window.innerHeight - popRect.height - pad);
    }
  }

  popover.style.left = `${Math.round(left)}px`;
  popover.style.top = `${Math.round(top)}px`;
}

function renderJJResult(data, anchorRect) {
  jjLoading.hidden = true;

  if (data.llm_unavailable || !data.jj) {
    jjBody.hidden = true;
    jjError.textContent =
      `${data.error || "Local/Cloud AI explanation unavailable."} ` +
      `You can configure Ollama or Gemini in Settings, or tap "英語を見る / Show English" below for instant offline JMdict definitions.`;
    jjError.hidden = false;
    if (anchorRect) positionPopover(anchorRect);
    return;
  }

  jjError.hidden = true;
  jjBody.hidden = false;
  const jj = data.jj;

  if (jj.part_of_speech) {
    posEl.textContent = jj.part_of_speech;
    posEl.hidden = false;
  }

  jjDef.textContent = jj.simple_definition || "";
  if (jj.context_note) {
    jjContext.textContent = jj.context_note;
    jjContext.hidden = false;
  } else {
    jjContext.hidden = true;
  }

  jjExamples.replaceChildren();
  for (const ex of jj.examples || []) {
    const li = document.createElement("li");
    li.textContent = ex.jp;
    if (ex.reading) {
      const rd = document.createElement("span");
      rd.className = "jj-ex-reading";
      rd.textContent = ex.reading;
      li.appendChild(rd);
    }
    jjExamples.appendChild(li);
  }

  const parts = [];
  if (data.provider && data.model) parts.push(`${data.provider}:${data.model}`);
  if (data.cached) parts.push("cached");
  jjMeta.textContent = parts.join(" · ");

  if (anchorRect) positionPopover(anchorRect);
}

function renderENResult(data, anchorRect) {
  enResults.replaceChildren();
  enResults.hidden = false;

  if (data.error && (!data.entries || !data.entries.length)) {
    const errBox = document.createElement("div");
    errBox.className = "inline-alert";
    errBox.textContent = data.error;
    enResults.appendChild(errBox);
    if (anchorRect) positionPopover(anchorRect);
    return;
  }

  if (!data.entries || !data.entries.length) {
    const p = document.createElement("p");
    p.className = "loading-line";
    p.textContent = `No offline JMdict entries found for "${data.lemma}".`;
    enResults.appendChild(p);
    if (anchorRect) positionPopover(anchorRect);
    return;
  }

  for (const entry of data.entries) {
    const wrap = document.createElement("div");
    const head = document.createElement("div");
    head.className = "en-entry-head";
    const k = entry.kanji?.length ? entry.kanji.join("・") : "";
    const r = entry.kana?.length ? entry.kana.join("・") : "";
    head.textContent = k && r ? `${k} 【${r}】` : k || r;
    wrap.appendChild(head);

    const ol = document.createElement("ol");
    ol.className = "en-senses";
    for (const sense of entry.senses || []) {
      const li = document.createElement("li");
      if (sense.pos?.length) {
        const posSpan = document.createElement("span");
        posSpan.className = "en-pos";
        posSpan.textContent = `[${sense.pos.slice(0, 2).join(", ")}]`;
        li.appendChild(posSpan);
      }
      li.appendChild(document.createTextNode((sense.glosses || []).join("; ")));
      ol.appendChild(li);
    }
    wrap.appendChild(ol);
    enResults.appendChild(wrap);
  }

  if (anchorRect) positionPopover(anchorRect);
}

async function fetchJJ(forceRegenerate = false) {
  if (!currentLookup) return;
  const nonce = ++lookupNonce;

  jjLoading.hidden = false;
  jjError.hidden = true;
  jjBody.hidden = true;
  positionPopover(currentLookup.anchorRect);

  try {
    const data = await api.lookupJJ({
      lemma: currentLookup.lemma,
      reading: currentLookup.reading,
      surface: currentLookup.surface,
      pos: currentLookup.pos,
      sentence_text: currentLookup.sentenceText,
      book_id: currentLookup.bookId,
      chapter_index: currentLookup.chapterIndex,
      sent_id: currentLookup.sentId,
      force_regenerate: forceRegenerate,
    });
    if (nonce !== lookupNonce || !currentLookup) return;
    renderJJResult(data, currentLookup.anchorRect);
  } catch (err) {
    if (nonce !== lookupNonce || !currentLookup) return;
    renderJJResult(
      { llm_unavailable: true, error: err.message, jj: null },
      currentLookup.anchorRect
    );
  }
}

export async function openWordLookup(tokenEl) {
  clearActiveToken();
  activeTokenEl = tokenEl;
  activeTokenEl.classList.add("active");

  const sentEl = tokenEl.closest(".sent");
  const lemma = tokenEl.dataset.lemma || tokenEl.textContent.trim();
  const normForm = tokenEl.dataset.norm || lemma;
  const surface = tokenEl.dataset.surface || tokenEl.textContent.trim();
  const reading = tokenEl.dataset.reading || "";
  const pos = tokenEl.dataset.pos || "";
  const sentId = sentEl?.dataset.sentId || null;
  const sentenceText = sentEl ? extractCleanText(sentEl) : "";
  const anchorRect = tokenEl.getBoundingClientRect();

  currentLookup = {
    lemma,
    normForm,
    surface,
    reading,
    pos,
    sentId,
    sentenceText,
    bookId: state.currentBook?.id ?? null,
    chapterIndex: state.currentChapter?.chapter_index ?? null,
    anchorRect,
  };

  lemmaEl.textContent = lemma;
  readingEl.textContent = reading && reading !== lemma ? `（${reading}）` : "";
  if (surface && surface !== lemma) {
    surfaceNoteEl.textContent = `本文: ${surface}`;
    surfaceNoteEl.hidden = false;
  } else {
    surfaceNoteEl.hidden = true;
  }
  posEl.textContent = pos;
  posEl.hidden = !pos;

  // Reset Step 2 (English remains hidden until explicitly requested)
  btnRevealEn.hidden = false;
  btnRevealEn.disabled = false;
  btnRevealEn.textContent = "英語を見る / Show English";
  enResults.hidden = true;
  enResults.replaceChildren();

  await fetchJJ(false);
}

export function initLookup({ onAskGrammar }) {
  onAskGrammarCb = onAskGrammar;

  btnClose.addEventListener("click", closeLookup);
  btnRegen.addEventListener("click", () => fetchJJ(true));

  btnRevealEn.addEventListener("click", async () => {
    if (!currentLookup) return;
    btnRevealEn.disabled = true;
    btnRevealEn.textContent = "Loading JMdict…";
    try {
      const data = await api.lookupEN({
        lemma: currentLookup.lemma,
        norm_form: currentLookup.normForm,
        surface: currentLookup.surface,
        reading: currentLookup.reading,
        book_id: currentLookup.bookId,
        chapter_index: currentLookup.chapterIndex,
        sent_id: currentLookup.sentId,
      });
      btnRevealEn.hidden = true;
      renderENResult(data, currentLookup.anchorRect);
    } catch (err) {
      btnRevealEn.disabled = false;
      btnRevealEn.textContent = "英語を見る / Show English";
      enResults.hidden = false;
      enResults.textContent = `Lookup error: ${err.message}`;
    }
  });

  btnAskGrammar.addEventListener("click", () => {
    if (!currentLookup || !onAskGrammarCb) return;
    const { sentId, sentenceText, surface } = currentLookup;
    closeLookup();
    if (sentId) {
      onAskGrammarCb({ sentId, sentenceText, targetText: surface });
    }
  });

  document.addEventListener("mousedown", (e) => {
    if (popover.hidden) return;
    if (popover.contains(e.target)) return;
    if (e.target.closest?.(".tok")) return;
    closeLookup();
  });
}
