/** Library view controller with Novels & Manga tabs and unified rescan. */

import { api } from "./api.js";
import { state } from "./state.js";

const libraryView = document.getElementById("library-view");
const readerView = document.getElementById("reader-view");
const mangaView = document.getElementById("manga-view");
const booksGrid = document.getElementById("books-grid");
const emptyEl = document.getElementById("library-empty");
const statusEl = document.getElementById("library-status");
const btnRescan = document.getElementById("btn-rescan");
const filterTabs = [...document.querySelectorAll(".lib-filter-tab")];

let currentFilter = "all"; // "all" | "novel" | "manga"
let onOpenBookCb = null;
let onOpenMangaCb = null;

function formatMinutes(seconds) {
  const mins = Math.floor((seconds || 0) / 60);
  if (mins <= 0) return "New";
  return `${mins} min read`;
}

function updateFilterCounts(books) {
  const novelCount = books.filter((b) => (b.media_type || "novel") !== "manga").length;
  const mangaCount = books.filter((b) => b.media_type === "manga").length;
  const counts = {
    all: books.length,
    novel: novelCount,
    manga: mangaCount,
  };
  const labels = {
    all: "All",
    novel: "Novels",
    manga: "Manga",
  };

  for (const btn of filterTabs) {
    const f = btn.dataset.filter || "all";
    btn.classList.toggle("active", f === currentFilter);
    btn.textContent = `${labels[f] || f} (${counts[f] ?? 0})`;
  }
}

export function renderBooks(books) {
  state.books = books || [];
  updateFilterCounts(state.books);

  const visible = state.books.filter((book) => {
    const mt = book.media_type || "novel";
    if (currentFilter === "novel") return mt !== "manga";
    if (currentFilter === "manga") return mt === "manga";
    return true;
  });

  booksGrid.replaceChildren();
  if (!visible.length) {
    emptyEl.hidden = false;
    return;
  }
  emptyEl.hidden = true;

  for (const book of visible) {
    const isManga = book.media_type === "manga";
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
      ph.textContent = isManga ? "漫" : "本";
      coverWrap.appendChild(ph);
    }

    if (isManga) {
      const badge = document.createElement("span");
      badge.className = "book-type-badge";
      badge.textContent = "MANGA";
      coverWrap.appendChild(badge);
    }

    const body = document.createElement("div");
    body.className = "book-card-body";

    const title = document.createElement("h3");
    title.className = "book-title";
    title.textContent = book.title;

    const author = document.createElement("p");
    author.className = "book-author";
    author.textContent = book.author || (isManga ? "Manga Volume" : "");

    const prog = book.progress || { chapter_index: 0, scroll_ratio: 0, reading_seconds: 0 };
    const meta = document.createElement("div");
    meta.className = "book-meta";
    if (isManga) {
      const pageTotal = book.page_count ?? book.chapter_count ?? 0;
      const curPage = Math.min(Math.max(1, (prog.chapter_index || 0) + 1), Math.max(1, pageTotal));
      meta.textContent = `${pageTotal} pages · Page ${curPage} · ${formatMinutes(prog.reading_seconds)}`;
    } else {
      const currentCh = book.chapters?.[prog.chapter_index];
      const chLabel = currentCh ? `Section: ${currentCh.title}` : `Section ${prog.chapter_index + 1}`;
      meta.textContent = `${book.chapter_count} sections · ${chLabel} · ${formatMinutes(prog.reading_seconds)}`;
    }

    const actions = document.createElement("div");
    actions.className = "book-card-actions";
    const btnOpen = document.createElement("button");
    btnOpen.type = "button";
    btnOpen.className = "btn btn-primary btn-block";
    btnOpen.textContent =
      prog.chapter_index > 0 || prog.scroll_ratio > 0.02
        ? "Continue Reading"
        : "Start Reading";
    btnOpen.addEventListener("click", async () => {
      btnOpen.disabled = true;
      statusEl.hidden = true;
      try {
        if (isManga) {
          if (onOpenMangaCb) await onOpenMangaCb(book.id, prog.chapter_index || 0);
        } else if (onOpenBookCb) {
          await onOpenBookCb(book.id, prog.chapter_index || 0, prog.scroll_ratio || 0);
        }
      } catch (err) {
        statusEl.textContent = `Could not open "${book.title}": ${err.message}`;
        statusEl.hidden = false;
      } finally {
        btnOpen.disabled = false;
      }
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
  if (document.fullscreenElement && document.exitFullscreen) {
    document.exitFullscreen().catch(() => {});
  }
  document.documentElement.classList.remove("manga-fullscreen", "mobile-chrome-hidden", "sel-tooltip-docked");
  if (window.location.hash) {
    history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  if (readerView) readerView.hidden = true;
  if (mangaView) mangaView.hidden = true;
  libraryView.hidden = false;
  loadLibrary();
}

export function initLibrary({ onOpenBook, onOpenManga }) {
  onOpenBookCb = onOpenBook;
  onOpenMangaCb = onOpenManga;

  for (const btn of filterTabs) {
    btn.addEventListener("click", () => {
      currentFilter = btn.dataset.filter || "all";
      renderBooks(state.books || []);
    });
  }

  btnRescan.addEventListener("click", async () => {
    btnRescan.disabled = true;
    statusEl.hidden = true;
    try {
      await Promise.all([api.scanBooks(), api.scanManga()]);
      const data = await api.listBooks();
      const books = data.books || [];
      renderBooks(books);
      const novels = books.filter((b) => (b.media_type || "novel") !== "manga").length;
      const manga = books.filter((b) => b.media_type === "manga").length;
      statusEl.textContent = `Scanned library: ${novels} novel(s), ${manga} manga volume(s) ready.`;
      statusEl.hidden = false;
    } catch (err) {
      statusEl.textContent = `Scan failed: ${err.message}`;
      statusEl.hidden = false;
    } finally {
      btnRescan.disabled = false;
    }
  });
}
