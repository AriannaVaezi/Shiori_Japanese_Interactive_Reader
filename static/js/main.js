/** Frontend application bootstrap wiring Library, Novel Reader, Manga Reader, Lookup, Grammar, Notes, Trouble Radar, Timer, and Mini-Games. */

import { api } from "./api.js";
import { closeGrammarDrawer, initGrammar, openGrammarForSentence } from "./grammar.js";
import { initLibrary, loadLibrary, showLibraryView } from "./library.js";
import { closeLookup, initLookup } from "./lookup.js";
import {
  closeMangaReader,
  goToMangaPage,
  initManga,
  openManga,
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
  scrollToSentence,
} from "./reader.js";
import { closeSettingsModal, initSettings, openSettingsModal } from "./settings.js";
import { state } from "./state.js";
import { initTimer, renderTimerUI, resetTimer } from "./timer.js";
import {
  closeTroubleModal,
  initTrouble,
  openTroubleModal,
  refreshTroubleBadge,
} from "./trouble.js";

async function leaveReaderToLibrary() {
  closeLookup();
  closeGrammarDrawer();
  await closeMangaReader({ flush: false });
  document.getElementById("reader-view").hidden = true;
  showLibraryView();
}

function refreshActiveHighlights() {
  refreshChapterHighlights();
  refreshMangaTokenHighlights();
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

async function bootstrap() {
  try {
    const settings = await api.getSettings();
    state.settings = { ...state.settings, ...settings };
  } catch {
    // fallback to client defaults
  }

  applyVisualSettings();
  initSettings();
  initGrammar();
  initTimer({
    onStartTimedQuiz: (triggerType) => launchQuiz(triggerType),
  });
  initTrouble({
    onLaunchQuiz: (triggerType) => launchQuiz(triggerType),
    onHighlightsChanged: refreshActiveHighlights,
  });
  initNotes({
    onJumpToNote: async ({ bookId, chapterIndex, sentId }) => {
      const mediaType = await resolveMediaType(bookId);
      const sameBook = state.currentBook?.id === bookId;
      const sameChap = state.currentChapter?.chapter_index === chapterIndex;

      if (mediaType === "manga") {
        const inMangaView = !document.getElementById("manga-view").hidden;
        if (!sameBook || !inMangaView) {
          await openManga(bookId, chapterIndex);
        } else if (!sameChap) {
          await goToMangaPage(chapterIndex);
        }
        return;
      }

      const inReaderView = !document.getElementById("reader-view").hidden;
      if (!sameBook || !inReaderView) {
        await openBook(bookId, chapterIndex, 0);
      } else if (!sameChap) {
        await openChapter(bookId, chapterIndex, 0);
      }
      if (sentId) {
        requestAnimationFrame(() => scrollToSentence(sentId));
      }
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
    onOpenTrouble: openTroubleModal,
    onOpenSettings: openSettingsModal,
    onOpenNotes: () => openNotesModal(),
    onPageChanged: () => {
      refreshTroubleBadge();
      refreshNotesBadge();
      refreshMangaTokenHighlights();
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

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeSettingsModal();
      closeTroubleModal();
      closeNotesModal();
    }
  });

  await loadLibrary();
}

bootstrap();
