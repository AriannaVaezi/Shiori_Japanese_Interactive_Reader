/** Context-aware inline grammar assistant drawer and chapter notes list. */

import { api } from "./api.js";
import { renderSafeMarkdown, state } from "./state.js";

const drawer = document.getElementById("grammar-drawer");
const btnToggle = document.getElementById("btn-toggle-grammar");
const btnClose = document.getElementById("btn-close-grammar");
const countBadge = document.getElementById("grammar-count-badge");
const mangaCountBadge = document.getElementById("manga-grammar-count-badge");
const drawerNotesCount = document.getElementById("drawer-notes-count");

const tabCurrent = document.getElementById("tab-grammar-current");
const tabNotes = document.getElementById("tab-grammar-notes");
const currentPane = document.getElementById("grammar-current-pane");
const notesPane = document.getElementById("grammar-notes-pane");

const emptyHint = document.getElementById("grammar-empty-hint");
const threadView = document.getElementById("grammar-thread-view");
const targetBadge = document.getElementById("grammar-target-badge");
const sentencePreview = document.getElementById("grammar-sentence-preview");
const statusNote = document.getElementById("grammar-status-note");
const messagesEl = document.getElementById("grammar-messages");
const form = document.getElementById("grammar-form");
const input = document.getElementById("grammar-input");
const btnSend = document.getElementById("btn-grammar-send");
const notesListEl = document.getElementById("grammar-notes-list");

let activeContext = null; // { bookId, chapterIndex, sentId, targetText, sentenceText }
let isAsking = false;

function switchTab(tab) {
  const showCurrent = tab === "current";
  tabCurrent.classList.toggle("active", showCurrent);
  tabNotes.classList.toggle("active", !showCurrent);
  currentPane.hidden = !showCurrent;
  notesPane.hidden = showCurrent;
}

function updateCountsAndSentenceMarks() {
  const count = state.grammarThreads.length;
  drawerNotesCount.textContent = String(count);
  countBadge.textContent = String(count);
  countBadge.hidden = count === 0;
  if (mangaCountBadge) {
    mangaCountBadge.textContent = String(count);
    mangaCountBadge.hidden = count === 0;
  }

  const activeSentIds = new Set(state.grammarThreads.map((t) => t.sent_id));
  document.querySelectorAll(".sent").forEach((el) => {
    el.classList.toggle("has-grammar", activeSentIds.has(el.dataset.sentId));
  });
  document.querySelectorAll(".manga-ocr-box").forEach((el) => {
    el.classList.toggle("has-grammar", activeSentIds.has(el.dataset.sentId));
  });
}

function renderMessages(messages, pending = false) {
  messagesEl.replaceChildren();
  for (const msg of messages || []) {
    const bubble = document.createElement("div");
    bubble.className = `grammar-msg ${msg.role === "user" ? "user" : "assistant"}`;
    if (msg.role === "user") {
      bubble.textContent = msg.content;
    } else {
      const mdWrap = document.createElement("div");
      renderSafeMarkdown(mdWrap, msg.content);
      bubble.appendChild(mdWrap);
      if (msg.provider && msg.model) {
        const meta = document.createElement("div");
        meta.className = "grammar-msg-meta";
        meta.textContent = `${msg.provider}:${msg.model}`;
        bubble.appendChild(meta);
      }
    }
    messagesEl.appendChild(bubble);
  }

  if (pending) {
    const load = document.createElement("div");
    load.className = "grammar-msg assistant loading-line";
    load.textContent = "Analyzing grammar and context…";
    messagesEl.appendChild(load);
  }

  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderNotesList() {
  notesListEl.replaceChildren();
  if (!state.grammarThreads.length) {
    const empty = document.createElement("div");
    empty.className = "drawer-empty";
    empty.textContent = "No grammar notes saved in this chapter yet.";
    notesListEl.appendChild(empty);
    return;
  }

  for (const thread of state.grammarThreads) {
    const item = document.createElement("div");
    item.className = "note-item";

    const head = document.createElement("div");
    head.className = "note-item-head";

    const label = document.createElement("span");
    label.className = "pos-badge";
    label.textContent = thread.target_text || "Full sentence";

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn-icon-sm";
    delBtn.title = "Delete note";
    delBtn.textContent = "×";
    delBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      try {
        await api.deleteGrammarThread(thread.id);
        state.grammarThreads = state.grammarThreads.filter((t) => t.id !== thread.id);
        if (
          activeContext &&
          activeContext.sentId === thread.sent_id &&
          activeContext.targetText === thread.target_text
        ) {
          activeContext = null;
          emptyHint.hidden = false;
          threadView.hidden = true;
        }
        updateCountsAndSentenceMarks();
        renderNotesList();
      } catch (err) {
        statusNote.textContent = `Could not delete note: ${err.message}`;
        statusNote.hidden = false;
      }
    });

    head.append(label, delBtn);

    const sent = document.createElement("p");
    sent.className = "note-item-sent";
    sent.textContent = thread.sentence_text;

    item.append(head, sent);
    item.addEventListener("click", () => {
      openThreadInDrawer(thread);
      const sentDom = document.querySelector(
        `.sent[data-sent-id="${CSS.escape(thread.sent_id)}"]`
      );
      sentDom?.scrollIntoView({ behavior: "smooth", block: "center", inline: "center" });
    });
    notesListEl.appendChild(item);
  }
}

function upsertThreadInState(thread) {
  if (!thread) return;
  const idx = state.grammarThreads.findIndex(
    (t) => t.sent_id === thread.sent_id && t.target_text === thread.target_text
  );
  if (idx >= 0) {
    state.grammarThreads[idx] = thread;
  } else {
    state.grammarThreads.unshift(thread);
  }
  updateCountsAndSentenceMarks();
  renderNotesList();
}

function openThreadInDrawer(thread) {
  activeContext = {
    bookId: thread.book_id,
    chapterIndex: thread.chapter_index,
    sentId: thread.sent_id,
    targetText: thread.target_text || "",
    sentenceText: thread.sentence_text,
  };
  drawer.hidden = false;
  switchTab("current");
  emptyHint.hidden = true;
  threadView.hidden = false;
  statusNote.hidden = true;

  if (activeContext.targetText) {
    targetBadge.textContent = `Focus: ${activeContext.targetText}`;
    targetBadge.hidden = false;
  } else {
    targetBadge.hidden = true;
  }
  sentencePreview.textContent = activeContext.sentenceText;
  renderMessages(thread.messages || []);
}

async function submitQuestion(questionText) {
  if (!activeContext || isAsking) return;
  isAsking = true;
  btnSend.disabled = true;
  statusNote.hidden = true;

  const existing = state.grammarThreads.find(
    (t) =>
      t.sent_id === activeContext.sentId && t.target_text === activeContext.targetText
  );
  const currentMsgs = existing ? [...existing.messages] : [];
  if (questionText) {
    currentMsgs.push({ role: "user", content: questionText });
  }
  renderMessages(currentMsgs, true);

  try {
    const res = await api.askGrammar({
      book_id: activeContext.bookId,
      chapter_index: activeContext.chapterIndex,
      sent_id: activeContext.sentId,
      target_text: activeContext.targetText,
      sentence_text: activeContext.sentenceText,
      question: questionText,
    });

    if (res.llm_unavailable) {
      statusNote.textContent =
        `${res.error || "AI backend unavailable."} Open Settings to configure llama.cpp, Ollama, or Gemini API.`;
      statusNote.hidden = false;
      renderMessages(res.thread?.messages || existing?.messages || []);
      return;
    }

    if (res.thread) {
      upsertThreadInState(res.thread);
      openThreadInDrawer(res.thread);
    }
  } catch (err) {
    statusNote.textContent = `Request failed: ${err.message}`;
    statusNote.hidden = false;
    renderMessages(existing?.messages || []);
  } finally {
    isAsking = false;
    btnSend.disabled = false;
  }
}

export async function loadChapterGrammarThreads(bookId, chapterIndex) {
  state.grammarThreads = [];
  activeContext = null;
  emptyHint.hidden = false;
  threadView.hidden = true;
  statusNote.hidden = true;
  try {
    const data = await api.getGrammarThreads(bookId, chapterIndex);
    state.grammarThreads = data.threads || [];
  } catch {
    state.grammarThreads = [];
  }
  updateCountsAndSentenceMarks();
  renderNotesList();
}

export async function openGrammarForSentence({
  sentId,
  sentenceText,
  targetText = "",
  autoAsk = true,
}) {
  const bookId = state.currentBook?.id;
  const chapterIndex = state.currentChapter?.chapter_index;
  if (bookId == null || chapterIndex == null) return;

  const cleanTarget = (targetText || "").trim();
  const existing = state.grammarThreads.find(
    (t) => t.sent_id === sentId && t.target_text === cleanTarget
  );

  if (existing && existing.messages?.length) {
    openThreadInDrawer(existing);
    if (!autoAsk) {
      requestAnimationFrame(() => input.focus());
    }
    return;
  }

  activeContext = {
    bookId,
    chapterIndex,
    sentId,
    targetText: cleanTarget,
    sentenceText,
  };
  drawer.hidden = false;
  switchTab("current");
  emptyHint.hidden = true;
  threadView.hidden = false;
  statusNote.hidden = true;

  if (cleanTarget) {
    targetBadge.textContent = `Focus: ${cleanTarget}`;
    targetBadge.hidden = false;
  } else {
    targetBadge.hidden = true;
  }
  sentencePreview.textContent = sentenceText;

  if (!autoAsk) {
    messagesEl.replaceChildren();
    const hint = document.createElement("div");
    hint.className = "drawer-empty";
    hint.textContent = "Passage context attached. Type your question below to start chatting.";
    messagesEl.appendChild(hint);
    requestAnimationFrame(() => input.focus());
    return;
  }

  await submitQuestion("");
}

export function dockGrammarDrawer(workspaceEl) {
  if (workspaceEl && drawer && drawer.parentElement !== workspaceEl) {
    workspaceEl.appendChild(drawer);
  }
}

export function toggleGrammarDrawer() {
  drawer.hidden = !drawer.hidden;
}

export function closeGrammarDrawer() {
  drawer.hidden = true;
}

export function initGrammar() {
  btnToggle.addEventListener("click", toggleGrammarDrawer);
  btnClose.addEventListener("click", closeGrammarDrawer);
  tabCurrent.addEventListener("click", () => switchTab("current"));
  tabNotes.addEventListener("click", () => {
    renderNotesList();
    switchTab("notes");
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (!q || isAsking) return;
    input.value = "";
    await submitQuestion(q);
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
}
