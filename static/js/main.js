/** Frontend application bootstrap wiring Library, Reader, Lookup, Grammar, Notes, Trouble Radar, Timer, and Mini-Games. */

import { api } from "./api.js";
import { closeGrammarDrawer, initGrammar, openGrammarForSentence } from "./grammar.js";
import { initLibrary, loadLibrary, showLibraryView } from "./library.js";
import { closeLookup, initLookup } from "./lookup.js";
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
import { closeTroubleModal, initTrouble, refreshTroubleBadge } from "./trouble.js";

function leaveReaderToLibrary() {
  closeLookup();
  closeGrammarDrawer();
  document.getElementById("reader-view").hidden = true;
  showLibraryView();
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
    onHighlightsChanged: () => refreshChapterHighlights(),
  });
  initNotes({
    onJumpToNote: async ({ bookId, chapterIndex, sentId }) => {
      const sameBook = state.currentBook?.id === bookId;
      const sameChap = state.currentChapter?.chapter_index === chapterIndex;
      if (!sameBook) {
        await openBook(bookId, chapterIndex, 0);
      } else if (!sameChap) {
        await openChapter(bookId, chapterIndex, 0);
      }
      if (sentId) {
        requestAnimationFrame(() => scrollToSentence(sentId));
      }
    },
    onNotesChanged: () => refreshChapterHighlights(),
  });
  initMiniGame({
    onQuizCompleted: () => {
      resetTimer();
      refreshTroubleBadge();
      refreshChapterHighlights();
    },
    onNextChapter: () => advanceChapter(1),
    onBackToLibrary: leaveReaderToLibrary,
  });
  initLookup({
    onAskGrammar: (ctx) => openGrammarForSentence(ctx),
    onSaveNote: (ctx) => openNotesModal(ctx),
    onLookupLogged: () => refreshTroubleBadge(),
  });
  initReader({
    onBackToLibrary: () => showLibraryView(),
    onChapterQuiz: (triggerType) => launchQuiz(triggerType),
    onSaveNote: (ctx) => openNotesModal(ctx),
    onChapterOpened: () => {
      refreshTroubleBadge();
      refreshNotesBadge();
      renderTimerUI();
    },
  });
  initLibrary({
    onOpenBook: (bookId, chIdx, scrollRatio) => openBook(bookId, chIdx, scrollRatio),
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
