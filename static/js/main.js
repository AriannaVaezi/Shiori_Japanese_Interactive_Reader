/** Frontend application bootstrap wiring Library, Novel Reader, Manga Reader, Lookup, Grammar, Notes, Trouble Radar, Timer, and Mini-Games. */

import { api } from "./api.js";
import { closeGrammarDrawer, initGrammar, openGrammarForSentence } from "./grammar.js";
import { initLibrary, loadLibrary, showLibraryView } from "./library.js";
import { closeLookup, initLookup } from "./lookup.js";
import {
  closeMangaReader,
  goToMangaPage,
  initManga,
  jumpToMangaHighlight,
  openManga,
  refreshMangaNoteMarks,
  refreshMangaTokenHighlights,
} from "./manga.js";
import { initMiniGame, launchQuiz } from "./minigame.js";
import { closeNotesModal, initNotes, openNotesModal, refreshNotesBadge } from "./notes.js";
import {
  advanceChapter,
  applyVisualSettings,
  initReader,
  openBook,
  openChapter,
  refreshChapterHighlights,
  scrollToHighlight,
} from "./reader.js";
import { closeSettingsModal, initSettings, openSettingsModal } from "./settings.js";
import { state } from "./state.js";
import { closeTimerPopover, initTimer, renderTimerUI, resetTimer } from "./timer.js";
import {
  closeTroubleModal,
  initTrouble,
  openTroubleModal,
  refreshTroubleBadge,
} from "./trouble.js";

async function leaveReaderToLibrary() {
  closeLookup();
  closeTimerPopover();
  closeGrammarDrawer();
  await closeMangaReader({ flush: false });
  document.getElementById("reader-view").hidden = true;
  showLibraryView();
}

async function restoreViewFromHash() {
  const hash = (window.location.hash || "").trim();
  if (!hash) return;
  const bookMatch = hash.match(/^#book\/(\d+)(?:\/(\d+))?$/);
  if (bookMatch) {
    const bookId = Number(bookMatch[1]);
    const saved = (state.books || []).find((b) => b.id === bookId)?.progress;
    const hashCh = bookMatch[2] != null ? Number(bookMatch[2]) : null;
    const chIdx = hashCh != null ? hashCh : Number(saved?.chapter_index || 0);
    const scrollRatio =
      saved && Number(saved.chapter_index) === chIdx ? Number(saved.scroll_ratio || 0) : 0;
    try {
      await openBook(bookId, chIdx, scrollRatio);
    } catch {
      showLibraryView();
    }
    return;
  }
  const mangaMatch = hash.match(/^#manga\/(\d+)(?:\/(\d+))?$/);
  if (mangaMatch) {
    const bookId = Number(mangaMatch[1]);
    const saved = (state.books || []).find((b) => b.id === bookId)?.progress;
    const pageIdx =
      mangaMatch[2] != null ? Number(mangaMatch[2]) : Number(saved?.chapter_index || 0);
    try {
      await openManga(bookId, pageIdx);
    } catch {
      showLibraryView();
    }
  }
}

function refreshActiveHighlights() {
  refreshChapterHighlights();
  refreshMangaTokenHighlights();
  refreshMangaNoteMarks();
}

async function resolveMediaType(bookId) {
  if (state.currentBook?.id === bookId && state.currentBook.media_type) {
    return state.currentBook.media_type;
  }
  const listed = (state.books || []).find((b) => b.id === bookId);
  if (listed?.media_type) {
    return listed.media_type;
  }
  try {
    const detail = await api.getBook(bookId);
    return detail.media_type || "novel";
  } catch {
    return "novel";
  }
}

const btnLlmStatus = document.getElementById("btn-llm-status");
const llmStatusLabel = document.getElementById("llm-status-label");
let llmStatusTimer = null;

async function refreshLLMStatus() {
  if (!btnLlmStatus || !llmStatusLabel) return;
  clearTimeout(llmStatusTimer);
  try {
    const info = await api.getLLMStatus();
    const st = info.status || "stopped";
    btnLlmStatus.dataset.status = st;
    btnLlmStatus.title = info.detail || "LLM Status (click to configure)";
    btnLlmStatus.hidden = false;
    if (st === "loading") {
      llmStatusLabel.textContent = info.model
        ? `AI: Loading (${info.model})…`
        : "AI: Loading…";
      llmStatusTimer = setTimeout(refreshLLMStatus, 2500);
    } else if (st === "ready") {
      llmStatusLabel.textContent =
        info.provider === "gemini" ? "AI: Gemini" : "AI: Ready";
    } else if (st === "external") {
      llmStatusLabel.textContent = "AI: Ollama";
    } else if (st === "unconfigured") {
      llmStatusLabel.textContent = "AI: Key Needed";
    } else if (st === "error") {
      llmStatusLabel.textContent = "AI: Offline";
    } else {
      llmStatusLabel.textContent = "AI: Off";
    }
  } catch {
    btnLlmStatus.hidden = true;
  }
}

async function bootstrap() {
  try {
    const settings = await api.getSettings();
    state.settings = { ...state.settings, ...settings };
  } catch {
    // fallback to client defaults
  }

  applyVisualSettings();
  initSettings({ onSettingsSaved: () => refreshLLMStatus() });
  initGrammar();
  initTimer({
    onStartTimedQuiz: (triggerType) => launchQuiz(triggerType),
  });
  initTrouble({
    onLaunchQuiz: (triggerType) => launchQuiz(triggerType),
    onHighlightsChanged: refreshActiveHighlights,
  });
  initNotes({
    onJumpToNote: async ({ bookId, chapterIndex, sentId, noteId, quoteText }) => {
      const mediaType = await resolveMediaType(bookId);
      const sameBook = state.currentBook?.id === bookId;
      const sameChap = state.currentChapter?.chapter_index === chapterIndex;

      if (mediaType === "manga") {
        await jumpToMangaHighlight({
          bookId,
          pageIndex: chapterIndex,
          sentId,
          noteId,
          quoteText,
        });
        return;
      }

      const inReaderView = !document.getElementById("reader-view").hidden;
      if (!sameBook || !inReaderView) {
        await openBook(bookId, chapterIndex, 0);
      } else if (!sameChap) {
        await openChapter(bookId, chapterIndex, 0);
      }
      requestAnimationFrame(() => scrollToHighlight({ noteId, sentId }));
    },
    onNotesChanged: refreshActiveHighlights,
  });
  initMiniGame({
    onQuizCompleted: () => {
      resetTimer();
      refreshTroubleBadge();
      refreshActiveHighlights();
    },
    onNextChapter: () => advanceChapter(1),
    onBackToLibrary: leaveReaderToLibrary,
  });
  initLookup({
    onAskGrammar: (ctx) => openGrammarForSentence(ctx),
    onSaveNote: (ctx) => openNotesModal(ctx),
    onLookupLogged: () => {
      refreshTroubleBadge();
      refreshMangaTokenHighlights();
    },
  });
  initReader({
    onBackToLibrary: () => showLibraryView(),
    onChapterQuiz: (triggerType) => launchQuiz(triggerType),
    onSaveNote: (ctx) => openNotesModal(ctx),
    onHighlightCreated: () => {
      refreshActiveHighlights();
      refreshNotesBadge();
    },
    onOpenManga: (bookId, pageIdx) => openManga(bookId, pageIdx),
    onChapterOpened: () => {
      refreshTroubleBadge();
      refreshNotesBadge();
      renderTimerUI();
    },
  });
  initManga({
    onBackToLibrary: () => showLibraryView(),
    onSaveNote: (ctx) => openNotesModal(ctx),
    onHighlightCreated: () => {
      refreshActiveHighlights();
      refreshNotesBadge();
    },
    onOpenTrouble: openTroubleModal,
    onOpenSettings: openSettingsModal,
    onOpenNotes: () => openNotesModal(),
    onPageChanged: () => {
      refreshTroubleBadge();
      refreshNotesBadge();
      refreshMangaTokenHighlights();
      refreshMangaNoteMarks();
    },
  });
  initLibrary({
    onOpenBook: (bookId, chIdx, scrollRatio) => openBook(bookId, chIdx, scrollRatio),
    onOpenManga: (bookId, pageIdx) => openManga(bookId, pageIdx),
  });

  document
    .getElementById("btn-open-settings-lib")
    .addEventListener("click", openSettingsModal);
  document
    .getElementById("btn-open-settings-reader")
    .addEventListener("click", openSettingsModal);
  document
    .getElementById("btn-open-notes-lib")
    .addEventListener("click", () => openNotesModal({ scope: "all" }));
  document
    .getElementById("btn-open-notes-reader")
    .addEventListener("click", () => openNotesModal());
  btnLlmStatus?.addEventListener("click", openSettingsModal);

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeSettingsModal();
      closeTroubleModal();
      closeNotesModal();
    }
  });

  refreshLLMStatus();
  await loadLibrary();
  await restoreViewFromHash();
}

bootstrap();
