/** Trouble Radar modal: inspect tracked trouble words/grammar, retire/restore items, and view quiz history. */

import { api } from "./api.js";
import { state } from "./state.js";

const btnOpen = document.getElementById("btn-open-trouble");
const countBadge = document.getElementById("trouble-count-badge");
const mangaCountBadge = document.getElementById("manga-trouble-count-badge");
const modal = document.getElementById("trouble-modal");
const btnClose = document.getElementById("btn-close-trouble");
const tabActive = document.getElementById("tab-trouble-active");
const tabRetired = document.getElementById("tab-trouble-retired");
const tabHistory = document.getElementById("tab-trouble-history");
const listEl = document.getElementById("trouble-list");
const btnLaunchQuiz = document.getElementById("btn-trouble-launch-quiz");

let currentTab = "active"; // "active" | "retired" | "history"
let onLaunchQuizCb = null;
let onHighlightsChangedCb = null;

function setTab(tab) {
  currentTab = tab;
  tabActive.classList.toggle("active", tab === "active");
  tabRetired.classList.toggle("active", tab === "retired");
  tabHistory.classList.toggle("active", tab === "history");
}

function formatMemoryStage(item) {
  if (item.retired || (item.memory_stage || 0) >= 3) return "Mastered";
  const stage = item.memory_stage || 0;
  if (stage === 0) return "New";
  if (!item.next_review_after) return `Stage ${stage} · Ready`;
  const diffMs = new Date(item.next_review_after).getTime() - Date.now();
  if (diffMs <= 0) return `Stage ${stage} · Ready`;
  const hrs = Math.ceil(diffMs / 3600000);
  const wait = hrs >= 48 ? `${Math.ceil(hrs / 24)}d` : `${hrs}h`;
  return `Stage ${stage} · next in ${wait}`;
}

function syncTroubleCountBadge(count) {
  countBadge.textContent = String(count);
  countBadge.hidden = count === 0;
  if (mangaCountBadge) {
    mangaCountBadge.textContent = String(count);
    mangaCountBadge.hidden = count === 0;
  }
}

export async function refreshTroubleBadge() {
  const bookId = state.currentBook?.id ?? null;
  try {
    const data = await api.getTrouble({ bookId, includeRetired: false, limit: 100 });
    state.troubleItems = data.items || [];
    syncTroubleCountBadge(state.troubleItems.length);
  } catch {
    // ignore background badge refresh errors
  }
}

async function renderCurrentTab() {
  listEl.replaceChildren();
  const bookId = state.currentBook?.id ?? null;

  if (currentTab === "history") {
    try {
      const res = await api.getQuizHistory(bookId, 20);
      const sessions = res.sessions || [];
      if (!sessions.length) {
        const p = document.createElement("p");
        p.className = "drawer-empty";
        p.textContent = "No completed mini-game rounds yet.";
        listEl.appendChild(p);
        return;
      }
      for (const s of sessions) {
        const row = document.createElement("div");
        row.className = "trouble-row";
        const main = document.createElement("div");
        main.className = "trouble-row-main";
        const title = document.createElement("div");
        title.className = "trouble-word";
        title.textContent = `Score: ${s.correct_count} / ${s.total_questions}`;
        const meta = document.createElement("div");
        meta.className = "trouble-meta";
        meta.textContent = `${s.trigger_type} · Section ${(s.chapter_index ?? 0) + 1} · ${new Date(s.created_at).toLocaleString()}`;
        main.append(title, meta);
        row.appendChild(main);
        listEl.appendChild(row);
      }
    } catch (err) {
      listEl.textContent = `Failed to load history: ${err.message}`;
    }
    return;
  }

  try {
    const res = await api.getTrouble({ bookId, includeRetired: true, limit: 120 });
    const allItems = res.items || [];
    const activeItems = allItems.filter((i) => !i.retired);
    state.troubleItems = activeItems;
    syncTroubleCountBadge(activeItems.length);

    const filtered = currentTab === "retired"
      ? allItems.filter((i) => i.retired)
      : activeItems;

    if (!filtered.length) {
      const p = document.createElement("p");
      p.className = "drawer-empty";
      p.textContent =
        currentTab === "retired"
          ? "No retired items yet. Ace trouble items in mini-games or mark them known to clear them!"
          : "No active trouble items yet. Look up words or ask grammar questions while reading!";
      listEl.appendChild(p);
      return;
    }

    for (const item of filtered) {
      const row = document.createElement("div");
      row.className = "trouble-row";

      const main = document.createElement("div");
      main.className = "trouble-row-main";

      const word = document.createElement("div");
      word.className = "trouble-word";
      const readingText =
        item.reading && item.reading !== item.display_text ? `（${item.reading}）` : "";
      word.textContent = `${item.display_text}${readingText}`;

      const meta = document.createElement("div");
      meta.className = "trouble-meta";
      const parts = [
        item.item_type === "grammar" ? "Grammar" : "Vocab",
        formatMemoryStage(item),
        `Score: ${Number(item.trouble_score || 0).toFixed(1)}`,
      ];
      if (item.jj_views) parts.push(`J-J: ${item.jj_views}`);
      if (item.en_reveals) parts.push(`EN: ${item.en_reveals}`);
      if (item.grammar_asks) parts.push(`Q&A: ${item.grammar_asks}`);
      if (item.correct_count || item.wrong_count) {
        parts.push(`Quiz: ${item.correct_count}✓/${item.wrong_count}✗`);
      }
      meta.textContent = parts.join(" · ");

      main.append(word, meta);

      if (item.sent_text) {
        const sent = document.createElement("div");
        sent.className = "trouble-sent";
        sent.textContent = item.sent_text;
        sent.title = item.sent_text;
        main.appendChild(sent);
      }

      const btnAction = document.createElement("button");
      btnAction.type = "button";
      btnAction.className = "btn btn-secondary";
      btnAction.textContent = item.retired ? "Restore" : "Mark Known";
      btnAction.addEventListener("click", async () => {
        btnAction.disabled = true;
        try {
          await api.setTroubleRetired(item.id, !item.retired);
          await renderCurrentTab();
          if (onHighlightsChangedCb) onHighlightsChangedCb();
        } finally {
          btnAction.disabled = false;
        }
      });

      row.append(main, btnAction);
      listEl.appendChild(row);
    }
  } catch (err) {
    listEl.textContent = `Could not load trouble radar: ${err.message}`;
  }
}

export async function openTroubleModal() {
  setTab("active");
  modal.hidden = false;
  await renderCurrentTab();
}

export function closeTroubleModal() {
  modal.hidden = true;
}

export function initTrouble({ onLaunchQuiz, onHighlightsChanged }) {
  onLaunchQuizCb = onLaunchQuiz;
  onHighlightsChangedCb = onHighlightsChanged;

  btnOpen.addEventListener("click", openTroubleModal);
  btnClose.addEventListener("click", closeTroubleModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeTroubleModal();
  });

  tabActive.addEventListener("click", () => {
    setTab("active");
    renderCurrentTab();
  });
  tabRetired.addEventListener("click", () => {
    setTab("retired");
    renderCurrentTab();
  });
  tabHistory.addEventListener("click", () => {
    setTab("history");
    renderCurrentTab();
  });

  btnLaunchQuiz.addEventListener("click", () => {
    closeTroubleModal();
    if (onLaunchQuizCb) onLaunchQuizCb("manual");
  });
}
