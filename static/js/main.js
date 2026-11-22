/** Frontend application bootstrap wiring Library, Reader, Lookup, Grammar, Trouble Radar, Timer, and Mini-Games. */

import { api } from "./api.js";
import { initGrammar, openGrammarForSentence } from "./grammar.js";
import { initLibrary, loadLibrary, showLibraryView } from "./library.js";
import { initLookup } from "./lookup.js";
import { initMiniGame, launchQuiz } from "./minigame.js";
import {
  advanceChapter,
  applyVisualSettings,
  initReader,
  openBook,
  refreshChapterHighlights,
} from "./reader.js";
import { closeSettingsModal, initSettings, openSettingsModal } from "./settings.js";
import { state } from "./state.js";
import { initTimer, renderTimerUI, resetTimer } from "./timer.js";
import { closeTroubleModal, initTrouble, refreshTroubleBadge } from "./trouble.js";

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
  initMiniGame({
    onQuizCompleted: () => {
      resetTimer();
      refreshTroubleBadge();
      refreshChapterHighlights();
    },
    onNextChapter: () => advanceChapter(1),
  });
  initLookup({
    onAskGrammar: (ctx) => openGrammarForSentence(ctx),
    onLookupLogged: () => refreshTroubleBadge(),
  });
  initReader({
    onBackToLibrary: () => showLibraryView(),
    onChapterQuiz: (triggerType) => launchQuiz(triggerType),
    onChapterOpened: () => {
      refreshTroubleBadge();
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

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeSettingsModal();
      closeTroubleModal();
    }
  });

  await loadLibrary();
}

bootstrap();
