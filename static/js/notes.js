/** Saved Quotes & Free-Form Reading Notes Notebook modal. */

import { api } from "./api.js";
import { state } from "./state.js";

const modal = document.getElementById("notes-modal");
const btnClose = document.getElementById("btn-close-notes");
const badgeEl = document.getElementById("notes-count-badge");
const mangaBadgeEl = document.getElementById("manga-notes-count-badge");

const tabChapter = document.getElementById("tab-notes-chapter");
const tabBook = document.getElementById("tab-notes-book");
const tabAll = document.getElementById("tab-notes-all");
const catBtns = [...document.querySelectorAll(".note-cat-btn")];

const composerHint = document.getElementById("note-composer-hint");
const composerFields = document.getElementById("note-composer-fields");
const form = document.getElementById("note-form");
const inputQuote = document.getElementById("input-note-quote");
const inputNote = document.getElementById("input-note-text");
const selectCategory = document.getElementById("select-note-category");
const btnCancelEdit = document.getElementById("btn-note-cancel-edit");
const btnSave = document.getElementById("btn-note-save");
const statusEl = document.getElementById("notes-status");
const listEl = document.getElementById("notes-list");

const CATEGORY_LABELS = {
  quote: "★ Quote",
  note: "📝 Note",
  expression: "💡 Expression",
};

let currentScope = "chapter"; // "chapter" | "book" | "all"
let currentCategory = "";
let editingNoteId = null;
let pendingComposerContext = null; // { bookId, chapterIndex, sentId }
let onJumpToNoteCb = null;
let onNotesChangedCb = null;

function setScopeTab(scope) {
  currentScope = scope;
  tabChapter.classList.toggle("active", scope === "chapter");
  tabBook.classList.toggle("active", scope === "book");
  tabAll.classList.toggle("active", scope === "all");
}

function setCategoryChip(cat) {
  currentCategory = cat;
  catBtns.forEach((btn) => {
    btn.classList.toggle("active", (btn.dataset.cat || "") === cat);
  });
}

function resetComposer(clearContext = true) {
  editingNoteId = null;
  inputQuote.value = "";
  inputNote.value = "";
  selectCategory.value = "quote";
  btnCancelEdit.hidden = true;
  btnSave.textContent = "Save Note";
  statusEl.hidden = true;
  if (clearContext) pendingComposerContext = null;
  syncComposerAvailability();
}

function syncComposerAvailability() {
  const hasBookContext =
    editingNoteId != null ||
    pendingComposerContext?.bookId != null ||
    state.currentBook?.id != null;
  composerHint.hidden = hasBookContext;
  composerFields.hidden = !hasBookContext;
}

export async function refreshNotesBadge() {
  const bookId = state.currentBook?.id ?? null;
  const chapterIndex = state.currentChapter?.chapter_index ?? null;
  if (bookId == null || chapterIndex == null) {
    badgeEl.hidden = true;
    if (mangaBadgeEl) mangaBadgeEl.hidden = true;
    return;
  }
  try {
    const res = await api.listNotes({ bookId, chapterIndex, limit: 200 });
    const count = (res.notes || []).length;
    badgeEl.textContent = String(count);
    badgeEl.hidden = count === 0;
    if (mangaBadgeEl) {
      mangaBadgeEl.textContent = String(count);
      mangaBadgeEl.hidden = count === 0;
    }
  } catch {
    // ignore transient count error
  }
}

async function fetchAndRenderNotes() {
  listEl.replaceChildren();
  statusEl.hidden = true;

  const query = { category: currentCategory, limit: 200 };
  if (currentScope === "chapter" && state.currentBook && state.currentChapter) {
    query.bookId = state.currentBook.id;
    query.chapterIndex = state.currentChapter.chapter_index;
  } else if (currentScope === "book" && state.currentBook) {
    query.bookId = state.currentBook.id;
  }

  try {
    const res = await api.listNotes(query);
    const notes = res.notes || [];
    state.notes = notes;

    if (!notes.length) {
      const p = document.createElement("p");
      p.className = "drawer-empty";
      p.textContent =
        currentScope === "all"
          ? "No saved quotes or notes yet. Highlight a passage while reading or write a free-form note above!"
          : "No saved quotes or notes in this view yet.";
      listEl.appendChild(p);
      return;
    }

    for (const note of notes) {
      const card = document.createElement("article");
      card.className = "note-card";

      const head = document.createElement("div");
      head.className = "note-card-head";

      const meta = document.createElement("div");
      meta.className = "note-card-meta";

      const catBadge = document.createElement("span");
      catBadge.className = "pos-badge";
      catBadge.textContent = CATEGORY_LABELS[note.category] || note.category;

      const loc = document.createElement("span");
      const chapLabel = note.chapter_title
        ? `${note.chapter_index + 1}. ${note.chapter_title}`
        : `Section ${note.chapter_index + 1}`;
      loc.textContent =
        currentScope === "all" ? `${note.book_title} · ${chapLabel}` : chapLabel;

      meta.append(catBadge, loc);

      const actions = document.createElement("div");
      actions.className = "note-card-actions";

      if (onJumpToNoteCb) {
        const btnJump = document.createElement("button");
        btnJump.type = "button";
        btnJump.className = "btn btn-ghost";
        btnJump.textContent = "Jump";
        btnJump.addEventListener("click", () => {
          closeNotesModal();
          onJumpToNoteCb({
            bookId: note.book_id,
            chapterIndex: note.chapter_index,
            sentId: note.sent_id,
          });
        });
        actions.appendChild(btnJump);
      }

      const btnEdit = document.createElement("button");
      btnEdit.type = "button";
      btnEdit.className = "btn btn-ghost";
      btnEdit.textContent = "Edit";
      btnEdit.addEventListener("click", () => {
        editingNoteId = note.id;
        inputQuote.value = note.quote_text || "";
        inputNote.value = note.note_text || "";
        selectCategory.value = note.category || "quote";
        btnCancelEdit.hidden = false;
        btnSave.textContent = "Update Note";
        syncComposerAvailability();
        inputNote.focus();
      });

      const btnDel = document.createElement("button");
      btnDel.type = "button";
      btnDel.className = "btn-icon-sm";
      btnDel.title = "Delete note";
      btnDel.textContent = "×";
      btnDel.addEventListener("click", async () => {
        btnDel.disabled = true;
        try {
          await api.deleteNote(note.id);
          if (editingNoteId === note.id) resetComposer();
          await fetchAndRenderNotes();
          await refreshNotesBadge();
          if (onNotesChangedCb) onNotesChangedCb();
        } catch (err) {
          statusEl.textContent = `Could not delete: ${err.message}`;
          statusEl.hidden = false;
        } finally {
          btnDel.disabled = false;
        }
      });

      actions.append(btnEdit, btnDel);
      head.append(meta, actions);
      card.appendChild(head);

      if (note.quote_text) {
        const blockquote = document.createElement("blockquote");
        blockquote.className = "note-quote-block";
        blockquote.textContent = note.quote_text;
        card.appendChild(blockquote);
      }

      if (note.note_text) {
        const body = document.createElement("p");
        body.className = "note-body-text";
        body.textContent = note.note_text;
        card.appendChild(body);
      }

      listEl.appendChild(card);
    }
  } catch (err) {
    statusEl.textContent = `Could not load notes: ${err.message}`;
    statusEl.hidden = false;
  }
}

export async function openNotesModal({
  scope = null,
  quoteText = "",
  noteText = "",
  category = "quote",
  sentId = null,
  bookId = null,
  chapterIndex = null,
} = {}) {
  resetComposer(false);
  const resolvedBookId = bookId ?? state.currentBook?.id ?? null;
  const resolvedChapterIdx = chapterIndex ?? state.currentChapter?.chapter_index ?? null;
  pendingComposerContext =
    resolvedBookId != null && resolvedChapterIdx != null
      ? { bookId: resolvedBookId, chapterIndex: resolvedChapterIdx, sentId }
      : null;

  const defaultScope = state.currentBook && state.currentChapter ? "chapter" : "all";
  const targetScope = scope || defaultScope;
  tabChapter.hidden = !(state.currentBook && state.currentChapter);
  tabBook.hidden = !state.currentBook;
  setScopeTab(targetScope);
  setCategoryChip("");

  inputQuote.value = quoteText;
  inputNote.value = noteText;
  selectCategory.value = quoteText ? category : "note";
  syncComposerAvailability();

  modal.hidden = false;
  await fetchAndRenderNotes();
  if (!composerFields.hidden) {
    if (quoteText) inputNote.focus();
    else inputNote.focus();
  }
}

export function closeNotesModal() {
  modal.hidden = true;
}

export function initNotes({ onJumpToNote, onNotesChanged }) {
  onJumpToNoteCb = onJumpToNote;
  onNotesChangedCb = onNotesChanged;

  btnClose.addEventListener("click", closeNotesModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeNotesModal();
  });

  tabChapter.addEventListener("click", () => {
    setScopeTab("chapter");
    fetchAndRenderNotes();
  });
  tabBook.addEventListener("click", () => {
    setScopeTab("book");
    fetchAndRenderNotes();
  });
  tabAll.addEventListener("click", () => {
    setScopeTab("all");
    fetchAndRenderNotes();
  });

  catBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      setCategoryChip(btn.dataset.cat || "");
      fetchAndRenderNotes();
    });
  });

  btnCancelEdit.addEventListener("click", () => resetComposer());

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const quoteText = inputQuote.value.trim();
    const noteText = inputNote.value.trim();
    const category = selectCategory.value || "quote";
    statusEl.hidden = true;

    if (!quoteText && !noteText) {
      statusEl.textContent = "Enter a book passage or write a note before saving.";
      statusEl.hidden = false;
      return;
    }

    btnSave.disabled = true;
    try {
      if (editingNoteId != null) {
        await api.updateNote(editingNoteId, {
          quote_text: quoteText,
          note_text: noteText,
          category,
        });
      } else {
        const bookId = pendingComposerContext?.bookId ?? state.currentBook?.id;
        const chapterIndex =
          pendingComposerContext?.chapterIndex ?? state.currentChapter?.chapter_index;
        if (bookId == null || chapterIndex == null) {
          throw new Error("Open a book chapter first to attach a note.");
        }
        await api.createNote({
          book_id: bookId,
          chapter_index: chapterIndex,
          sent_id: pendingComposerContext?.sentId ?? null,
          quote_text: quoteText,
          note_text: noteText,
          category,
        });
      }

      resetComposer();
      await fetchAndRenderNotes();
      await refreshNotesBadge();
      if (onNotesChangedCb) onNotesChangedCb();
    } catch (err) {
      statusEl.textContent = err.message;
      statusEl.hidden = false;
    } finally {
      btnSave.disabled = false;
    }
  });
}
