/** Adaptive 5-question chapter-end / timed mini-game (Cloze, Bunsetsu Scramble, Particle Fix). */

import { api } from "./api.js";
import { state } from "./state.js";

const modal = document.getElementById("quiz-modal");
const titleEl = document.getElementById("quiz-title");
const progressLabel = document.getElementById("quiz-progress-label");
const progressFill = document.getElementById("quiz-progress-fill");
const btnClose = document.getElementById("btn-close-quiz");

const loadingEl = document.getElementById("quiz-loading");
const errorEl = document.getElementById("quiz-error");
const stageEl = document.getElementById("quiz-stage");
const kindBadge = document.getElementById("quiz-kind-badge");
const btnHint = document.getElementById("btn-quiz-hint");
const hintBox = document.getElementById("quiz-hint-box");
const promptSentence = document.getElementById("quiz-prompt-sentence");

const choicesEl = document.getElementById("quiz-choices");
const scrambleEl = document.getElementById("quiz-scramble");
const scrambleTray = document.getElementById("quiz-scramble-tray");
const scrambleBank = document.getElementById("quiz-scramble-bank");
const btnScrambleClear = document.getElementById("btn-scramble-clear");
const btnScrambleCheck = document.getElementById("btn-scramble-check");

const feedbackEl = document.getElementById("quiz-feedback");
const feedbackBadge = document.getElementById("quiz-feedback-badge");
const feedbackFull = document.getElementById("quiz-feedback-full");
const feedbackExp = document.getElementById("quiz-feedback-exp");
const btnNext = document.getElementById("btn-quiz-next");

const summaryEl = document.getElementById("quiz-summary");
const scoreNumEl = document.getElementById("quiz-score-num");
const scoreSubEl = document.getElementById("quiz-score-sub");
const conqueredWrap = document.getElementById("quiz-summary-conquered");
const conqueredList = document.getElementById("quiz-conquered-list");
const trickyWrap = document.getElementById("quiz-summary-tricky");
const trickyList = document.getElementById("quiz-tricky-list");
const btnDone = document.getElementById("btn-quiz-done");
const btnNextChap = document.getElementById("btn-quiz-next-chap");

let sessionState = null;
let onQuizCompletedCb = null;
let onNextChapterCb = null;

function renderScrambleChips() {
  if (!sessionState) return;
  const q = sessionState.questions[sessionState.index];
  const selectedIndices = sessionState.scrambleSelected;

  scrambleTray.replaceChildren();
  if (!selectedIndices.length) {
    const ph = document.createElement("span");
    ph.className = "hint-text";
    ph.textContent = "Tap chunks below in order…";
    scrambleTray.appendChild(ph);
  } else {
    selectedIndices.forEach((bankIdx, trayPos) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chunk-chip";
      chip.textContent = q.chunks[bankIdx];
      chip.disabled = sessionState.answered;
      chip.addEventListener("click", () => {
        if (sessionState.answered) return;
        sessionState.scrambleSelected.splice(trayPos, 1);
        renderScrambleChips();
      });
      scrambleTray.appendChild(chip);
    });
  }

  scrambleBank.replaceChildren();
  q.chunks.forEach((text, bankIdx) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chunk-chip";
    chip.textContent = text;
    chip.disabled = sessionState.answered || selectedIndices.includes(bankIdx);
    chip.addEventListener("click", () => {
      if (sessionState.answered) return;
      sessionState.scrambleSelected.push(bankIdx);
      renderScrambleChips();
    });
    scrambleBank.appendChild(chip);
  });

  btnScrambleCheck.disabled =
    sessionState.answered || selectedIndices.length !== q.chunks.length;
  btnScrambleClear.disabled = sessionState.answered || !selectedIndices.length;
}

function showFeedback(isCorrect, q) {
  sessionState.answered = true;
  sessionState.results.push({
    question_id: q.question_id,
    kind: q.kind,
    trouble_item_id: q.trouble_item_id ?? null,
    correct: isCorrect,
  });

  feedbackEl.hidden = false;
  feedbackBadge.className = `quiz-feedback-badge ${isCorrect ? "ok" : "err"}`;
  feedbackBadge.textContent = isCorrect ? "✓ Correct!" : "✗ Not quite";
  feedbackFull.textContent = q.full_sentence || "";
  feedbackExp.textContent = q.explanation && q.explanation !== q.full_sentence ? q.explanation : "";
  btnNext.textContent =
    sessionState.index + 1 >= sessionState.questions.length ? "Finish Round →" : "Next →";
}

function renderQuestion() {
  const q = sessionState.questions[sessionState.index];
  const total = sessionState.questions.length;
  sessionState.answered = false;
  sessionState.scrambleSelected = [];

  progressLabel.textContent = `Question ${sessionState.index + 1} of ${total}`;
  progressFill.style.width = `${Math.round(((sessionState.index + 1) / total) * 100)}%`;

  stageEl.hidden = false;
  summaryEl.hidden = true;
  feedbackEl.hidden = true;
  hintBox.hidden = true;

  kindBadge.textContent = q.prompt_label || q.kind;
  if (q.hint) {
    btnHint.hidden = false;
    hintBox.textContent = q.hint;
  } else {
    btnHint.hidden = true;
  }

  if (q.kind === "scramble") {
    promptSentence.textContent = "Arrange the sentence chunks into the natural order:";
    choicesEl.hidden = true;
    scrambleEl.hidden = false;
    renderScrambleChips();
    return;
  }

  promptSentence.textContent = q.blanked_sentence || "";
  scrambleEl.hidden = true;
  choicesEl.hidden = false;
  choicesEl.replaceChildren();

  (q.choices || []).forEach((choice, idx) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn quiz-choice-btn";
    btn.dataset.choice = choice;
    btn.textContent = `${idx + 1}. ${choice}`;
    btn.addEventListener("click", () => {
      if (sessionState.answered) return;
      const isCorrect = choice === q.correct_answer;
      [...choicesEl.children].forEach((b) => {
        b.disabled = true;
        if (b.dataset.choice === q.correct_answer) b.classList.add("correct");
        else if (b === btn && !isCorrect) b.classList.add("wrong");
      });
      showFeedback(isCorrect, q);
    });
    choicesEl.appendChild(btn);
  });
}

async function finishRound() {
  stageEl.hidden = true;
  summaryEl.hidden = false;
  progressLabel.textContent = "Round Complete";
  progressFill.style.width = "100%";

  let summary = {
    score: sessionState.results.filter((r) => r.correct).length,
    total: sessionState.results.length,
    conquered: [],
    still_tricky: [],
  };

  try {
    summary = await api.submitQuiz({
      book_id: sessionState.bookId,
      chapter_index: sessionState.chapterIndex,
      trigger_type: sessionState.triggerType,
      results: sessionState.results,
    });
  } catch {
    // show local score even if submit fails transiently
  }

  scoreNumEl.textContent = `${summary.score} / ${summary.total}`;
  scoreSubEl.textContent =
    summary.score === summary.total
      ? "Clean sweep! Zero flashcard backlog."
      : "Round complete — missed items stay lightly highlighted for next time.";

  conqueredList.replaceChildren();
  if (summary.conquered?.length) {
    conqueredWrap.hidden = false;
    for (const item of summary.conquered) {
      const li = document.createElement("li");
      li.textContent = item.retired
        ? `${item.display_text} (Mastered & retired!)`
        : `${item.display_text} (Score lowered -> ${Number(item.trouble_score).toFixed(1)})`;
      conqueredList.appendChild(li);
    }
  } else {
    conqueredWrap.hidden = true;
  }

  trickyList.replaceChildren();
  if (summary.still_tricky?.length) {
    trickyWrap.hidden = false;
    for (const item of summary.still_tricky) {
      const li = document.createElement("li");
      li.textContent = `${item.display_text} (Score: ${Number(item.trouble_score).toFixed(1)})`;
      trickyList.appendChild(li);
    }
  } else {
    trickyWrap.hidden = true;
  }

  const hasNextChapter =
    state.currentBook &&
    state.currentChapter &&
    state.currentChapter.chapter_index + 1 < state.currentChapter.total_chapters;
  btnNextChap.hidden = !hasNextChapter;

  if (onQuizCompletedCb) onQuizCompletedCb(summary);
}

export async function launchQuiz(triggerType = "chapter") {
  const bookId = state.currentBook?.id;
  const chapterIndex = state.currentChapter?.chapter_index;
  if (bookId == null || chapterIndex == null) return;

  titleEl.textContent =
    triggerType === "timer"
      ? "Timed Reading Review"
      : triggerType === "manual"
      ? "Trouble Radar Quiz"
      : "Chapter Review";
  progressLabel.textContent = "";
  progressFill.style.width = "0%";
  errorEl.hidden = true;
  stageEl.hidden = true;
  summaryEl.hidden = true;
  loadingEl.hidden = false;
  modal.hidden = false;

  try {
    const data = await api.generateQuiz({
      book_id: bookId,
      chapter_index: chapterIndex,
      trigger_type: triggerType,
      max_questions: 5,
    });
    loadingEl.hidden = true;

    const questions = data.questions || [];
    if (!questions.length) {
      errorEl.textContent =
        "This section doesn't have enough text for a quiz yet (e.g. an illustration page). Try a story chapter!";
      errorEl.hidden = false;
      return;
    }

    sessionState = {
      bookId,
      chapterIndex,
      triggerType,
      questions,
      index: 0,
      results: [],
      answered: false,
      scrambleSelected: [],
    };
    renderQuestion();
  } catch (err) {
    loadingEl.hidden = true;
    errorEl.textContent = `Could not generate quiz: ${err.message}`;
    errorEl.hidden = false;
  }
}

export function closeQuizModal() {
  modal.hidden = true;
  sessionState = null;
}

export function initMiniGame({ onQuizCompleted, onNextChapter }) {
  onQuizCompletedCb = onQuizCompleted;
  onNextChapterCb = onNextChapter;

  btnClose.addEventListener("click", closeQuizModal);
  btnDone.addEventListener("click", closeQuizModal);
  btnNextChap.addEventListener("click", () => {
    closeQuizModal();
    if (onNextChapterCb) onNextChapterCb();
  });

  btnHint.addEventListener("click", () => {
    hintBox.hidden = !hintBox.hidden;
  });

  btnScrambleClear.addEventListener("click", () => {
    if (!sessionState || sessionState.answered) return;
    sessionState.scrambleSelected = [];
    renderScrambleChips();
  });

  btnScrambleCheck.addEventListener("click", () => {
    if (!sessionState || sessionState.answered) return;
    const q = sessionState.questions[sessionState.index];
    const userOrder = sessionState.scrambleSelected.map((i) => q.chunks[i]);
    const isCorrect =
      userOrder.length === q.correct_answer.length &&
      userOrder.every((chunk, idx) => chunk === q.correct_answer[idx]);
    renderScrambleChips();
    showFeedback(isCorrect, q);
    btnScrambleCheck.disabled = true;
    btnScrambleClear.disabled = true;
  });

  btnNext.addEventListener("click", () => {
    if (!sessionState) return;
    sessionState.index += 1;
    if (sessionState.index >= sessionState.questions.length) {
      finishRound();
    } else {
      renderQuestion();
    }
  });

  document.addEventListener("keydown", (e) => {
    if (modal.hidden) return;
    if (e.key === "Escape") {
      closeQuizModal();
      return;
    }
    if (!sessionState) return;
    if (!sessionState.answered && ["1", "2", "3", "4"].includes(e.key)) {
      const idx = parseInt(e.key, 10) - 1;
      const choiceBtn = choicesEl.children[idx];
      if (!choicesEl.hidden && choiceBtn && !choiceBtn.disabled) {
        choiceBtn.click();
      }
    } else if (sessionState.answered && e.key === "Enter") {
      e.preventDefault();
      btnNext.click();
    }
  });
}
