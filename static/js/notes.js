/** Saved Quotes, Highlights & Free-Form Reading Notes Notebook modal. */

import { api } from "./api.js";
import {
  HIGHLIGHT_COLORS,
  applyRangeHighlightMarkup,
  rawOffsetToPlainOffset,
  renderHighlightedText,
  state,
  stripHighlightMarkup,
} from "./state.js";

const modal = document.getElementById("notes-modal");
const btnClose = document.getElementById("btn-close-notes");
const badgeEl = document.getElementById("notes-count-badge");
const mangaBadgeEl = document.getElementById("manga-notes-count-badge");

const tabChapter = document.getElementById("tab-notes-chapter");
const tabBook = document.getElementById("tab-notes-book");
const tabAll = document.getElementById("tab-notes-all");
const catBtns = [...document.querySelectorAll(".note-cat-btn")];
const colorFilterBtns = [...document.querySelectorAll(".note-color-filter-btn")];

const composerHint = document.getElementById("note-composer-hint");
const composerFields = document.getElementById("note-composer-fields");
const form = document.getElementById("note-form");
const inputQuote = document.getElementById("input-note-quote");
const inputNote = document.getElementById("input-note-text");
const selectCategory = document.getElementById("select-note-category");
const colorPickBtns = [...document.querySelectorAll(".note-color-pick-btn")];
const inlineSwatchBtns = [...document.querySelectorAll(".note-inline-swatch-btn")];
const btnCancelEdit = document.getElementById("btn-note-cancel-edit");
const btnSave = document.getElementById("btn-note-save");
const statusEl = document.getElementById("notes-status");
const listEl = document.getElementById("notes-list");

const noteHlPopover = document.getElementById("note-hl-popover");
const cardHlBtns = [...document.querySelectorAll(".note-card-hl-btn")];

const CATEGORY_LABELS = {
  quote: "★ Quote",
  note: "📝 Note",
  expression: "💡 Expression",
};

const COLOR_LABELS = {
  yellow: "Yellow",
  blue: "Blue",
  pink: "Pink",
  orange: "Orange",
  green: "Green",
  purple: "Purple",
};

let currentScope = "chapter"; // "chapter" | "book" | "all"
let currentCategory = "";
let currentColorFilter = "";
let composerColor = "yellow";
let editingNoteId = null;
let pendingComposerContext = null; // { bookId, chapterIndex, sentId }
let lastComposerInput = inputNote;
let pendingCardSelection = null; // { noteId, fieldKey, plainStart, plainEnd }
const notesById = new Map();
let onJumpToNoteCb = null;
let onNotesChangedCb = null;

function normalizeHighlightColor(color) {
  return HIGHLIGHT_COLORS.includes(color) ? color : "";
}

function compactCoreQuote(text) {
  const plain = stripHighlightMarkup(text || "").replace(/\s+/g, "");
  const stripped = plain.replace(
    /^[。、．，.,!?！？「」『』（）()【】［］\[\]・…—―\-〜~"'“”‘’]+|[。、．，.,!?！？「」『』（）()【】［］\[\]・…—―\-〜~"'“”‘’]+$/g,
    ""
  );
  return stripped || plain;
}

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

function setColorFilter(color) {
  currentColorFilter = normalizeHighlightColor(color);
  colorFilterBtns.forEach((btn) => {
    btn.classList.toggle("active", (btn.dataset.color || "") === currentColorFilter);
  });
}

function setComposerColor(color) {
  composerColor = color === "" ? "" : normalizeHighlightColor(color) || "yellow";
  colorPickBtns.forEach((btn) => {
    btn.classList.toggle("active", (btn.dataset.color || "") === composerColor);
  });
}

function hideNoteHlPopover() {
  pendingCardSelection = null;
  if (noteHlPopover) noteHlPopover.hidden = true;
}

function resetComposer(clearContext = true) {
  editingNoteId = null;
  inputQuote.value = "";
  inputNote.value = "";
  selectCategory.value = "quote";
  setComposerColor("yellow");
  lastComposerInput = inputNote;
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

function applyInlineHighlightToComposer(color) {
  const active = document.activeElement;
  const target =
    active === inputQuote || active === inputNote
      ? active
      : lastComposerInput === inputQuote
        ? inputQuote
        : inputNote;
  const rawText = target.value || "";
  const selStart = target.selectionStart ?? 0;
  const selEnd = target.selectionEnd ?? 0;
  if (selStart === selEnd) {
    statusEl.textContent =
      "Select text inside the Passage or Note box first to highlight it.";
    statusEl.hidden = false;
    target.focus();
    return;
  }
  const plainStart = rawOffsetToPlainOffset(rawText, selStart);
  const plainEnd = rawOffsetToPlainOffset(rawText, selEnd);
  if (plainStart >= plainEnd) return;

  target.value = applyRangeHighlightMarkup(rawText, plainStart, plainEnd, color || null);
  statusEl.hidden = true;
  target.focus();
}

function inspectSavedCardSelection() {
  if (!noteHlPopover || modal.hidden) {
    hideNoteHlPopover();
    return;
  }
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
    hideNoteHlPopover();
    return;
  }
  const range = sel.getRangeAt(0);
  const startNode =
    range.startContainer.nodeType === Node.ELEMENT_NODE
      ? range.startContainer
      : range.startContainer.parentElement;
  const fieldEl = startNode?.closest?.(".note-quote-block, .note-body-text");
  if (
    !fieldEl ||
    !listEl.contains(fieldEl) ||
    !fieldEl.contains(range.endContainer)
  ) {
    hideNoteHlPopover();
    return;
  }

  const preStart = document.createRange();
  preStart.selectNodeContents(fieldEl);
  preStart.setEnd(range.startContainer, range.startOffset);
  const plainStart = preStart.toString().length;

  const preEnd = document.createRange();
  preEnd.selectNodeContents(fieldEl);
  preEnd.setEnd(range.endContainer, range.endOffset);
  const plainEnd = preEnd.toString().length;

  if (plainStart >= plainEnd || !range.toString().trim()) {
    hideNoteHlPopover();
    return;
  }

  const noteId = Number(fieldEl.dataset.noteId);
  const fieldKey = fieldEl.dataset.noteField;
  if (!noteId || !fieldKey || !notesById.has(noteId)) {
    hideNoteHlPopover();
    return;
  }

  const rect = range.getBoundingClientRect();
  if (!rect || (rect.width === 0 && rect.height === 0)) {
    hideNoteHlPopover();
    return;
  }

  pendingCardSelection = { noteId, fieldKey, plainStart, plainEnd };
  noteHlPopover.hidden = false;
  const popW = noteHlPopover.offsetWidth || 236;
  const popH = noteHlPopover.offsetHeight || 40;
  const left = Math.min(
    Math.max(12, rect.left + rect.width / 2 - popW / 2),
    Math.max(12, window.innerWidth - popW - 12)
  );
  let top = rect.top - popH - 8;
  if (top < 12) {
    top = Math.min(window.innerHeight - popH - 12, rect.bottom + 8);
  }
  noteHlPopover.style.left = `${Math.round(left)}px`;
  noteHlPopover.style.top = `${Math.round(top)}px`;
}

async function fetchAndRenderNotes() {
  hideNoteHlPopover();
  listEl.replaceChildren();
  notesById.clear();
  statusEl.hidden = true;

  const query = {
    category: currentCategory,
    color: currentColorFilter,
    limit: 200,
  };
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
      if (currentColorFilter) {
        p.textContent = `No ${COLOR_LABELS[currentColorFilter] || ""} highlights in this view yet.`;
      } else {
        p.textContent =
          currentScope === "all"
            ? "No saved highlights or notes yet. Highlight a passage while reading or write a free-form note above!"
            : "No saved highlights or notes in this view yet.";
      }
      listEl.appendChild(p);
      return;
    }

    for (const note of notes) {
      notesById.set(note.id, note);
      const cardColor = normalizeHighlightColor(note.color);

      const card = document.createElement("article");
      card.className = "note-card";
      card.dataset.noteId = String(note.id);
      if (cardColor) card.dataset.hlColor = cardColor;

      const head = document.createElement("div");
      head.className = "note-card-head";

      const meta = document.createElement("div");
      meta.className = "note-card-meta";

      if (cardColor) {
        const dot = document.createElement("span");
        dot.className = `note-color-dot hl-${cardColor}`;
        dot.title = `${COLOR_LABELS[cardColor]} highlight`;
        meta.appendChild(dot);
      }

      const catBadge = document.createElement("span");
      catBadge.className = "pos-badge";
      catBadge.textContent = CATEGORY_LABELS[note.category] || note.category;

      const loc = document.createElement("span");
      const chapLabel =
        note.media_type === "manga"
          ? note.chapter_title
            ? `Page ${note.chapter_index + 1} · ${note.chapter_title}`
            : `Page ${note.chapter_index + 1}`
          : note.chapter_title
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
            noteId: note.id,
            quoteText: note.quote_text || "",
          });
        });
        actions.appendChild(btnJump);
      }

      if (cardColor) {
        const btnUnhl = document.createElement("button");
        btnUnhl.type = "button";
        btnUnhl.className = "btn btn-ghost";
        btnUnhl.title = note.note_text?.trim()
          ? "Remove highlight color (keeps written note)"
          : "Remove highlight and entry from notes";
        btnUnhl.textContent = "Unhighlight";
        btnUnhl.addEventListener("click", async () => {
          btnUnhl.disabled = true;
          try {
            await api.unhighlightNotes({
              book_id: note.book_id,
              chapter_index: note.chapter_index,
              note_ids: [note.id],
            });
            if (editingNoteId === note.id) resetComposer();
            await fetchAndRenderNotes();
            await refreshNotesBadge();
            if (onNotesChangedCb) onNotesChangedCb();
          } catch (err) {
            statusEl.textContent = `Could not unhighlight: ${err.message}`;
            statusEl.hidden = false;
          } finally {
            btnUnhl.disabled = false;
          }
        });
        actions.appendChild(btnUnhl);
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
        setComposerColor(cardColor);
        lastComposerInput = inputNote;
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
        blockquote.dataset.noteId = String(note.id);
        blockquote.dataset.noteField = "quote_text";
        renderHighlightedText(blockquote, note.quote_text);
        card.appendChild(blockquote);
      }

      if (note.note_text) {
        const body = document.createElement("p");
        body.className = "note-body-text";
        body.dataset.noteId = String(note.id);
        body.dataset.noteField = "note_text";
        renderHighlightedText(body, note.note_text);
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
  color = "yellow",
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
  setColorFilter("");

  let matchedExisting = null;
  const targetCore = compactCoreQuote(quoteText);
  if (quoteText && resolvedBookId != null && resolvedChapterIdx != null && targetCore) {
    try {
      const existingRes = await api.listNotes({
        bookId: resolvedBookId,
        chapterIndex: resolvedChapterIdx,
        limit: 200,
      });
      const candidates = existingRes.notes || [];
      const sameSentOrOpen = candidates.filter((n) => {
        const nCore = compactCoreQuote(n.quote_text);
        if (!nCore) return false;
        const sentOk = !sentId || !n.sent_id || n.sent_id === sentId;
        const eq = nCore === targetCore;
        const contained =
          (nCore.includes(targetCore) || targetCore.includes(nCore)) &&
          Math.min(nCore.length, targetCore.length) >= 2;
        return (eq || contained) && (sentOk || Math.min(nCore.length, targetCore.length) >= 4);
      });
      matchedExisting =
        sameSentOrOpen.find((n) => (n.note_text || "").trim()) || sameSentOrOpen[0] || null;
    } catch {
      matchedExisting = null;
    }
  }

  if (matchedExisting) {
    editingNoteId = matchedExisting.id;
    inputQuote.value = matchedExisting.quote_text || quoteText;
    inputNote.value = matchedExisting.note_text || noteText;
    selectCategory.value = matchedExisting.category || category;
    setComposerColor(normalizeHighlightColor(matchedExisting.color));
    btnCancelEdit.hidden = false;
    btnSave.textContent = "Update Note";
  } else {
    inputQuote.value = quoteText;
    inputNote.value = noteText;
    selectCategory.value = quoteText ? category : "note";
    setComposerColor(color === "" ? "" : normalizeHighlightColor(color) || "yellow");
  }
  lastComposerInput = inputNote;
  syncComposerAvailability();

  modal.hidden = false;
  await fetchAndRenderNotes();
  if (!composerFields.hidden) {
    inputNote.focus();
  }
}

export function closeNotesModal() {
  hideNoteHlPopover();
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

  colorFilterBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      setColorFilter(btn.dataset.color || "");
      fetchAndRenderNotes();
    });
  });

  colorPickBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      setComposerColor(btn.dataset.color ?? "");
    });
  });

  [inputQuote, inputNote].forEach((input) => {
    input.addEventListener("focus", () => {
      lastComposerInput = input;
    });
    input.addEventListener("select", () => {
      lastComposerInput = input;
    });
  });

  inlineSwatchBtns.forEach((btn) => {
    btn.addEventListener("mousedown", (e) => {
      // Preserve current textarea/input text selection when clicking a swatch
      e.preventDefault();
    });
    btn.addEventListener("click", () => {
      applyInlineHighlightToComposer(btn.dataset.hlColor || "");
    });
  });

  listEl.addEventListener("mouseup", () => {
    requestAnimationFrame(inspectSavedCardSelection);
  });
  listEl.addEventListener("scroll", hideNoteHlPopover);

  document.addEventListener("pointerdown", (e) => {
    if (modal.hidden) return;
    if (noteHlPopover && !noteHlPopover.contains(e.target)) {
      hideNoteHlPopover();
    }
  });

  cardHlBtns.forEach((btn) => {
    btn.addEventListener("mousedown", (e) => e.preventDefault());
    btn.addEventListener("click", async () => {
      const sel = pendingCardSelection;
      if (!sel) {
        hideNoteHlPopover();
        return;
      }
      const note = notesById.get(sel.noteId);
      if (!note) {
        hideNoteHlPopover();
        return;
      }
      const color = btn.dataset.color || "";
      const currentRaw = note[sel.fieldKey] || "";
      const updatedRaw = applyRangeHighlightMarkup(
        currentRaw,
        sel.plainStart,
        sel.plainEnd,
        color || null
      );
      try {
        await api.updateNote(note.id, { [sel.fieldKey]: updatedRaw });
        if (editingNoteId === note.id) {
          if (sel.fieldKey === "quote_text") inputQuote.value = updatedRaw;
          else inputNote.value = updatedRaw;
        }
        window.getSelection()?.removeAllRanges();
        hideNoteHlPopover();
        await fetchAndRenderNotes();
        if (onNotesChangedCb) onNotesChangedCb();
      } catch (err) {
        statusEl.textContent = `Could not highlight note text: ${err.message}`;
        statusEl.hidden = false;
        hideNoteHlPopover();
      }
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
          color: composerColor,
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
          color: composerColor,
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
