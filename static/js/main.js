/** Frontend application bootstrap wiring Library, Reader, Lookup, Grammar, and Settings. */

import { api } from "./api.js";
import { initGrammar, openGrammarForSentence } from "./grammar.js";
import { initLibrary, loadLibrary, showLibraryView } from "./library.js";
import { initLookup } from "./lookup.js";
import { applyVisualSettings, initReader, openBook } from "./reader.js";
import { closeSettingsModal, initSettings, openSettingsModal } from "./settings.js";
import { state } from "./state.js";

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
  initLookup({
    onAskGrammar: (ctx) => openGrammarForSentence(ctx),
  });
  initReader({
    onBackToLibrary: () => showLibraryView(),
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
    if (e.key === "Escape") closeSettingsModal();
  });

  await loadLibrary();
}

bootstrap();
