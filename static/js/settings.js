/** Settings modal for switching between local Ollama and Gemini API. */

import { api } from "./api.js";
import { state } from "./state.js";

const modal = document.getElementById("settings-modal");
const form = document.getElementById("settings-form");
const btnClose = document.getElementById("btn-close-settings");
const btnCancel = document.getElementById("btn-cancel-settings");
const btnSave = document.getElementById("btn-save-settings");

const ollamaFields = document.getElementById("settings-ollama-fields");
const geminiFields = document.getElementById("settings-gemini-fields");

const inputOllamaUrl = document.getElementById("input-ollama-url");
const btnRefreshOllama = document.getElementById("btn-refresh-ollama");
const selectOllamaModel = document.getElementById("select-ollama-model");
const ollamaHint = document.getElementById("ollama-model-hint");

const inputGeminiKey = document.getElementById("input-gemini-key");
const btnClearGeminiKey = document.getElementById("btn-clear-gemini-key");
const geminiKeyHint = document.getElementById("gemini-key-hint");
const selectGeminiModel = document.getElementById("select-gemini-model");

const btnTest = document.getElementById("btn-test-llm");
const testResult = document.getElementById("llm-test-result");

let clearGeminiFlag = false;

function getSelectedProvider() {
  const checked = form.querySelector('input[name="llm_provider"]:checked');
  return checked ? checked.value : "ollama";
}

function setSelectedProvider(provider) {
  const target = provider === "gemini" ? "gemini" : "ollama";
  form.querySelectorAll('input[name="llm_provider"]').forEach((r) => {
    r.checked = r.value === target;
  });
  ollamaFields.hidden = target !== "ollama";
  geminiFields.hidden = target !== "gemini";
}

async function refreshOllamaModels(url, selectedModel) {
  selectOllamaModel.replaceChildren();
  const autoOpt = document.createElement("option");
  autoOpt.value = "";
  autoOpt.textContent = "Auto-detect best local model";
  selectOllamaModel.appendChild(autoOpt);

  try {
    const res = await api.getOllamaModels(url);
    if (!res.reachable) {
      ollamaHint.textContent = `Ollama not reachable at ${res.url}. Start Ollama or switch to Gemini API.`;
      if (selectedModel) {
        const opt = document.createElement("option");
        opt.value = selectedModel;
        opt.textContent = `${selectedModel} (saved)`;
        selectOllamaModel.appendChild(opt);
        selectOllamaModel.value = selectedModel;
      }
      return;
    }

    for (const m of res.models || []) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m === res.suggested ? `${m} (suggested)` : m;
      selectOllamaModel.appendChild(opt);
    }

    if (selectedModel) {
      if (![...selectOllamaModel.options].some((o) => o.value === selectedModel)) {
        const opt = document.createElement("option");
        opt.value = selectedModel;
        opt.textContent = `${selectedModel} (saved)`;
        selectOllamaModel.appendChild(opt);
      }
      selectOllamaModel.value = selectedModel;
    }

    ollamaHint.textContent = res.models?.length
      ? `${res.models.length} local model(s) detected.`
      : "Ollama is running, but no models are installed yet. Run e.g. `ollama pull qwen3:8b`.";
  } catch (err) {
    ollamaHint.textContent = `Failed to list models: ${err.message}`;
  }
}

export async function openSettingsModal() {
  clearGeminiFlag = false;
  testResult.textContent = "";
  testResult.className = "test-result";

  try {
    const latest = await api.getSettings();
    state.settings = { ...state.settings, ...latest };
  } catch {
    // use cached state
  }

  const s = state.settings;
  setSelectedProvider(s.llm_provider);
  inputOllamaUrl.value = s.ollama_url || "http://localhost:11434";
  inputGeminiKey.value = "";
  selectGeminiModel.value = s.gemini_model || "gemini-2.5-flash";

  if (s.has_gemini_key) {
    geminiKeyHint.textContent = `Saved key: ${s.gemini_api_key_masked} (leave blank to keep current key)`;
    btnClearGeminiKey.hidden = false;
  } else {
    geminiKeyHint.textContent = "Get an API key from Google AI Studio (aistudio.google.com).";
    btnClearGeminiKey.hidden = true;
  }

  modal.hidden = false;
  await refreshOllamaModels(inputOllamaUrl.value, s.ollama_model || "");
}

export function closeSettingsModal() {
  modal.hidden = true;
}

export function initSettings() {
  form.querySelectorAll('input[name="llm_provider"]').forEach((radio) => {
    radio.addEventListener("change", () => setSelectedProvider(getSelectedProvider()));
  });

  btnRefreshOllama.addEventListener("click", () => {
    refreshOllamaModels(inputOllamaUrl.value.trim(), selectOllamaModel.value);
  });

  btnClearGeminiKey.addEventListener("click", () => {
    clearGeminiFlag = true;
    inputGeminiKey.value = "";
    btnClearGeminiKey.hidden = true;
    geminiKeyHint.textContent = "Key will be cleared on Save.";
  });

  btnTest.addEventListener("click", async () => {
    btnTest.disabled = true;
    testResult.className = "test-result";
    testResult.textContent = "Testing connection…";
    try {
      const payload = {
        llm_provider: getSelectedProvider(),
        ollama_url: inputOllamaUrl.value.trim(),
        ollama_model: selectOllamaModel.value,
        gemini_model: selectGeminiModel.value,
      };
      if (inputGeminiKey.value.trim()) {
        payload.gemini_api_key = inputGeminiKey.value.trim();
      }
      const res = await api.testLLM(payload);
      if (res.ok) {
        testResult.className = "test-result ok";
        testResult.textContent = `Connected (${res.provider}:${res.model}) -> "${res.sample}"`;
      } else {
        testResult.className = "test-result err";
        testResult.textContent = res.error || "Connection failed.";
      }
    } catch (err) {
      testResult.className = "test-result err";
      testResult.textContent = err.message;
    } finally {
      btnTest.disabled = false;
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    btnSave.disabled = true;
    try {
      const payload = {
        llm_provider: getSelectedProvider(),
        ollama_url: inputOllamaUrl.value.trim() || "http://localhost:11434",
        ollama_model: selectOllamaModel.value,
        gemini_model: selectGeminiModel.value,
        clear_gemini_key: clearGeminiFlag,
      };
      if (inputGeminiKey.value.trim()) {
        payload.gemini_api_key = inputGeminiKey.value.trim();
      }
      const saved = await api.updateSettings(payload);
      state.settings = { ...state.settings, ...saved };
      closeSettingsModal();
    } catch (err) {
      testResult.className = "test-result err";
      testResult.textContent = `Could not save: ${err.message}`;
    } finally {
      btnSave.disabled = false;
    }
  });

  btnClose.addEventListener("click", closeSettingsModal);
  btnCancel.addEventListener("click", closeSettingsModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeSettingsModal();
  });
}
