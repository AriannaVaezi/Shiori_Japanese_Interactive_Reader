/** Library view controller. */

import { api } from "./api.js";
import { state } from "./state.js";

const libraryView = document.getElementById("library-view");
const booksGrid = document.getElementById("books-grid");
const emptyEl = document.getElementById("library-empty");
const statusEl = document.getElementById("library-status");
const btnRescan = document.getElementById("btn-rescan");

let onOpenBookCb = null;

function formatMinutes(seconds) {
  const mins = Math.floor((seconds || 0) / 60);
  if (mins <= 0) return "New";
  return `${mins} min read`;
}

export function renderBooks(books) {
  state.books = books;
  booksGrid.replaceChildren();

  if (!books.length) {
    emptyEl.hidden = false;
    return;
  }
  emptyEl.hidden = true;

  for (const book of books) {
    const card = document.createElement("article");
    card.className = "book-card";

    const coverWrap = document.createElement("div");
    coverWrap.className = "book-cover-wrap";
    if (book.cover_url) {
      const img = document.createElement("img");
      img.src = book.cover_url;
      img.alt = book.title;
      img.loading = "lazy";
      coverWrap.appendChild(img);
    } else {
      const ph = document.createElement("span");
      ph.className = "book-cover-placeholder";
      ph.textContent = "本";
      coverWrap.appendChild(ph);
    }

    const body = document.createElement("div");
    body.className = "book-card-body";

    const title = document.createElement("h3");
    title.className = "book-title";
    title.textContent = book.title;

    const author = document.createElement("p");
    author.className = "book-author";
    author.textContent = book.author;

    const prog = book.progress || { chapter_index: 0, scroll_ratio: 0, reading_seconds: 0 };
    const currentCh = book.chapters?.[prog.chapter_index];
    const chLabel = currentCh ? `Section: ${currentCh.title}` : `Section ${prog.chapter_index + 1}`;
    const meta = document.createElement("div");
    meta.className = "book-meta";
    meta.textContent = `${book.chapter_count} sections · ${chLabel} · ${formatMinutes(prog.reading_seconds)}`;

    const actions = document.createElement("div");
    actions.className = "book-card-actions";
    const btnOpen = document.createElement("button");
    btnOpen.type = "button";
    btnOpen.className = "btn btn-primary btn-block";
    btnOpen.textContent =
      prog.chapter_index > 0 || prog.scroll_ratio > 0.02
        ? "Continue Reading"
        : "Start Reading";
    btnOpen.addEventListener("click", () => {
      if (onOpenBookCb) onOpenBookCb(book.id, prog.chapter_index || 0, prog.scroll_ratio || 0);
    });
    actions.appendChild(btnOpen);

    body.append(title, author, meta, actions);
    card.append(coverWrap, body);
    booksGrid.appendChild(card);
  }
}

export async function loadLibrary() {
  try {
    const data = await api.listBooks();
    renderBooks(data.books || []);
  } catch (err) {
    statusEl.textContent = `Could not load library: ${err.message}`;
    statusEl.hidden = false;
  }
}

export function showLibraryView() {
  document.getElementById("reader-view").hidden = true;
  libraryView.hidden = false;
  loadLibrary();
}

export function initLibrary({ onOpenBook }) {
  onOpenBookCb = onOpenBook;
  btnRescan.addEventListener("click", async () => {
    btnRescan.disabled = true;
    statusEl.hidden = true;
    try {
      const data = await api.scanBooks();
      renderBooks(data.books || []);
      statusEl.textContent = `Scanned books/: ${data.books.length} EPUB(s) ready.`;
      statusEl.hidden = false;
    } catch (err) {
      statusEl.textContent = `Scan failed: ${err.message}`;
      statusEl.hidden = false;
    } finally {
      btnRescan.disabled = false;
    }
  });
}
