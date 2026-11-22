/** Idle-aware active reading session timer and review trigger pill. */

import { api } from "./api.js";
import { state } from "./state.js";

const readerView = document.getElementById("reader-view");
const btnPill = document.getElementById("btn-timer-pill");
const popover = document.getElementById("timer-popover");
const statusText = document.getElementById("timer-status-text");
const btnPause = document.getElementById("btn-timer-pause");
const btnReset = document.getElementById("btn-timer-reset");
const btnQuiz = document.getElementById("btn-timer-quiz");
const intervalBtns = [...document.querySelectorAll(".timer-int-btn")];

let elapsedSeconds = 0;
let paused = false;
let intervalId = null;
let onStartQuizCb = null;

function getTargetMinutes() {
  const mins = parseInt(state.settings.quiz_timer_minutes ?? "15", 10);
  return Number.isNaN(mins) ? 15 : Math.max(0, mins);
}

function formatMMSS(totalSec) {
  const m = Math.floor(totalSec / 60);
  const s = totalSec % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

export function renderTimerUI() {
  const targetMins = getTargetMinutes();
  const targetSec = targetMins * 60;
  const reached = targetSec > 0 && elapsedSeconds >= targetSec;

  if (targetMins === 0) {
    btnPill.textContent = `⏱ ${formatMMSS(elapsedSeconds)}`;
    btnPill.classList.remove("timer-ready");
  } else if (reached) {
    btnPill.textContent = `⏱ Review Ready (${formatMMSS(elapsedSeconds)})`;
    btnPill.classList.add("timer-ready");
  } else {
    btnPill.textContent = `⏱ ${formatMMSS(elapsedSeconds)} / ${targetMins}m`;
    btnPill.classList.remove("timer-ready");
  }

  const idle = Date.now() - state.lastActivityMs > 60000;
  if (paused) {
    statusText.textContent = "Paused";
  } else if (idle) {
    statusText.textContent = "Idle (auto-paused)";
  } else if (reached) {
    statusText.textContent = "Goal reached — quiz ready";
  } else {
    statusText.textContent = "Active";
  }

  btnPause.textContent = paused ? "Resume" : "Pause";
  intervalBtns.forEach((btn) => {
    btn.classList.toggle("active", parseInt(btn.dataset.mins, 10) === targetMins);
  });
}

export function resetTimer() {
  elapsedSeconds = 0;
  paused = false;
  renderTimerUI();
}

export function initTimer({ onStartTimedQuiz }) {
  onStartQuizCb = onStartTimedQuiz;

  btnPill.addEventListener("click", (e) => {
    e.stopPropagation();
    popover.hidden = !popover.hidden;
    renderTimerUI();
  });

  document.addEventListener("mousedown", (e) => {
    if (popover.hidden) return;
    if (popover.contains(e.target) || btnPill.contains(e.target)) return;
    popover.hidden = true;
  });

  btnPause.addEventListener("click", () => {
    paused = !paused;
    state.lastActivityMs = Date.now();
    renderTimerUI();
  });

  btnReset.addEventListener("click", () => {
    resetTimer();
  });

  btnQuiz.addEventListener("click", () => {
    popover.hidden = true;
    if (onStartQuizCb) onStartQuizCb("timer");
  });

  intervalBtns.forEach((btn) => {
    btn.addEventListener("click", async () => {
      const mins = btn.dataset.mins || "15";
      state.settings.quiz_timer_minutes = mins;
      renderTimerUI();
      try {
        const saved = await api.updateSettings({ quiz_timer_minutes: mins });
        state.settings = { ...state.settings, ...saved };
      } catch {
        // keep optimistic setting
      }
    });
  });

  if (intervalId) clearInterval(intervalId);
  intervalId = setInterval(() => {
    if (readerView.hidden || paused || document.visibilityState !== "visible") {
      return;
    }
    if (Date.now() - state.lastActivityMs > 60000) {
      renderTimerUI();
      return;
    }
    elapsedSeconds += 1;
    renderTimerUI();
  }, 1000);

  renderTimerUI();
}
