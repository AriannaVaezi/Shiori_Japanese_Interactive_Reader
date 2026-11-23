/** Settings modal for switching between llama.cpp, Ollama, and Gemini API. */

import { api } from "./api.js";
import { state } from "./state.js";

const modal = document.getElementById("settings-modal");
const form = document.getElementById("settings-form");
const btnClose = document.getElementById("btn-close-settings");
const btnCancel = document.getElementById("btn-cancel-settings");
const btnSave = document.getElementById("btn-save-settings");

const llamacppFields = document.getElementById("settings-llamacpp-fields");
const ollamaFields = document.getElementById("settings-ollama-fields");
const geminiFields = document.getElementById("settings-gemini-fields");

const inputLlamaUrl = document.getElementById("input-llamacpp-url");
const btnRefreshLlama = document.getElementById("btn-refresh-llamacpp");
const selectLlamaModel = document.getElementById("select-llamacpp-model");
const llamacppHint = document.getElementById("llamacpp-model-hint");
const inputLlamaModelPath = document.getElementById("input-llamacpp-model-path");
const chkLlamaAutostart = document.getElementById("chk-llamacpp-autostart");
const inputLlamaKey = document.getElementById("input-llamacpp-key");
const btnClearLlamaKey = document.getElementById("btn-clear-llamacpp-key");
const llamacppKeyHint = document.getElementById("llamacpp-key-hint");

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
let clearLlamaKeyFlag = false;

function getSelectedProvider() {
  const checked = form.querySelector('input[name="llm_provider"]:checked');
  return checked ? checked.value : "llamacpp";
}

function setSelectedProvider(provider) {
  const target = ["llamacpp", "ollama", "gemini"].includes(provider) ? provider : "llamacpp";
  form.querySelectorAll('input[name="llm_provider"]').forEach((r) => {
    r.checked = r.value === target;
  });
  llamacppFields.hidden = target !== "llamacpp";
  ollamaFields.hidden = target !== "ollama";
  geminiFields.hidden = target !== "gemini";
}

function populateSelect(selectEl, models, suggested, selectedModel, autoLabel) {
  selectEl.replaceChildren();
  const autoOpt = document.createElement("option");
  autoOpt.value = "";
  autoOpt.textContent = autoLabel;
  selectEl.appendChild(autoOpt);

  for (const m of models || []) {
    const opt = document.createElement("option");
    opt.value = m;
    opt.textContent = m === suggested ? `${m} (suggested)` : m;
    selectEl.appendChild(opt);
  }

  if (selectedModel) {
    if (![...selectEl.options].some((o) => o.value === selectedModel)) {
      const opt = document.createElement("option");
      opt.value = selectedModel;
      opt.textContent = `${selectedModel} (saved)`;
      selectEl.appendChild(opt);
    }
    selectEl.value = selectedModel;
  }
}

async function refreshLlamaCppModels(url, selectedModel) {
  populateSelect(selectLlamaModel, [], null, selectedModel, "Auto / server default");
  try {
    const res = await api.getLlamaCppModels(url);
    if (!res.reachable) {
      llamacppHint.textContent = `llama.cpp not reachable at ${res.url}. Run: llama-server -hf Qwen/Qwen3-8B-GGUF:Q4_K_M --port 8080 -ngl 99`;
      return;
    }
    populateSelect(
      selectLlamaModel,
      res.models,
      res.suggested,
      selectedModel,
      "Auto / server default"
    );
    llamacppHint.textContent = res.models?.length
      ? `Connected: ${res.models.length} model(s) loaded (${res.models[0]}).`
      : "Server reachable (using loaded default model).";
  } catch (err) {
    llamacppHint.textContent = `Could not reach llama.cpp: ${err.message}`;
  }
}

async function refreshOllamaModels(url, selectedModel) {
  populateSelect(selectOllamaModel, [], null, selectedModel, "Auto-detect best local model");
  try {
    const res = await api.getOllamaModels(url);
    if (!res.reachable) {
      ollamaHint.textContent = `Ollama not reachable at ${res.url}. Start Ollama or switch to llama.cpp / Gemini API.`;
      return;
    }
    populateSelect(
      selectOllamaModel,
      res.models,
      res.suggested,
      selectedModel,
      "Auto-detect best local model"
    );
    ollamaHint.textContent = res.models?.length
      ? `${res.models.length} local model(s) detected.`
      : "Ollama is running, but no models are installed yet. Run e.g. `ollama pull qwen3:8b`.";
  } catch (err) {
    ollamaHint.textContent = `Failed to list models: ${err.message}`;
  }
}

export async function openSettingsModal() {
  clearGeminiFlag = false;
  clearLlamaKeyFlag = false;
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

  inputLlamaUrl.value = s.llamacpp_url || "http://127.0.0.1:8080";
  inputLlamaModelPath.value = s.llamacpp_model_path || "";
  chkLlamaAutostart.checked = s.llamacpp_autostart !== "false";
  inputLlamaKey.value = "";
  if (s.has_llamacpp_key) {
    llamacppKeyHint.textContent = `Saved token: ${s.llamacpp_api_key_masked} (leave blank to keep)`;
    btnClearLlamaKey.hidden = false;
  } else {
    llamacppKeyHint.textContent = "Leave blank unless your llama-server uses --api-key.";
    btnClearLlamaKey.hidden = true;
  }

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

  populateSelect(selectLlamaModel, [], null, s.llamacpp_model || "", "Auto / server default");
  populateSelect(selectOllamaModel, [], null, s.ollama_model || "", "Auto-detect best local model");

  modal.hidden = false;
  if (s.llm_provider === "ollama") {
    await refreshOllamaModels(inputOllamaUrl.value, s.ollama_model || "");
  } else if (s.llm_provider === "llamacpp") {
    await refreshLlamaCppModels(inputLlamaUrl.value, s.llamacpp_model || "");
  }
}

export function closeSettingsModal() {
  modal.hidden = true;
}

export function initSettings() {
  form.querySelectorAll('input[name="llm_provider"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      const p = getSelectedProvider();
      setSelectedProvider(p);
      if (p === "llamacpp") {
        refreshLlamaCppModels(inputLlamaUrl.value.trim(), selectLlamaModel.value);
      } else if (p === "ollama") {
        refreshOllamaModels(inputOllamaUrl.value.trim(), selectOllamaModel.value);
      }
    });
  });

  btnRefreshLlama.addEventListener("click", () => {
    refreshLlamaCppModels(inputLlamaUrl.value.trim(), selectLlamaModel.value);
  });

  btnClearLlamaKey.addEventListener("click", () => {
    clearLlamaKeyFlag = true;
    inputLlamaKey.value = "";
    btnClearLlamaKey.hidden = true;
    llamacppKeyHint.textContent = "Token will be cleared on Save.";
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
    const selectedProvider = getSelectedProvider();
    testResult.textContent =
      selectedProvider === "llamacpp"
        ? "Starting llama.cpp (if needed) & testing connection…"
        : "Testing connection…";
    try {
      const payload = {
        llm_provider: selectedProvider,
        llamacpp_url: inputLlamaUrl.value.trim(),
        llamacpp_model: selectLlamaModel.value,
        ollama_url: inputOllamaUrl.value.trim(),
        ollama_model: selectOllamaModel.value,
        gemini_model: selectGeminiModel.value,
      };
      if (inputLlamaKey.value.trim()) {
        payload.llamacpp_api_key = inputLlamaKey.value.trim();
      }
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
    const selectedProvider = getSelectedProvider();
    btnSave.textContent =
      selectedProvider === "llamacpp" ? "Saving & starting llama.cpp…" : "Saving…";
    try {
      const payload = {
        llm_provider: selectedProvider,
        llamacpp_url: inputLlamaUrl.value.trim() || "http://127.0.0.1:8080",
        llamacpp_model: selectLlamaModel.value,
        llamacpp_model_path: inputLlamaModelPath.value.trim(),
        llamacpp_autostart: chkLlamaAutostart.checked ? "true" : "false",
        clear_llamacpp_key: clearLlamaKeyFlag,
        ollama_url: inputOllamaUrl.value.trim() || "http://localhost:11434",
        ollama_model: selectOllamaModel.value,
        gemini_model: selectGeminiModel.value,
        clear_gemini_key: clearGeminiFlag,
      };
      if (inputLlamaKey.value.trim()) {
        payload.llamacpp_api_key = inputLlamaKey.value.trim();
      }
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
      btnSave.textContent = "Save Settings";
    }
  });

  btnClose.addEventListener("click", closeSettingsModal);
  btnCancel.addEventListener("click", closeSettingsModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeSettingsModal();
  });
}
