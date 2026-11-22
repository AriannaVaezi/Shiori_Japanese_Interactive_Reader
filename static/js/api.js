/** Thin JSON API client for the FastAPI backend. */

async function request(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const resp = await fetch(url, { ...options, headers });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const errData = await resp.json();
      if (errData?.detail) detail = String(errData.detail);
    } catch {
      // ignore non-JSON error body
    }
    throw new Error(detail);
  }
  return resp.json();
}

export const api = {
  listBooks() {
    return request("/api/books");
  },
  scanBooks() {
    return request("/api/books/scan", { method: "POST" });
  },
  getBook(bookId) {
    return request(`/api/books/${bookId}`);
  },
  getChapter(bookId, chapterIndex) {
    return request(`/api/books/${bookId}/chapters/${chapterIndex}`);
  },
  saveProgress(bookId, payload) {
    return request(`/api/books/${bookId}/progress`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  lookupJJ(payload) {
    return request("/api/lookup/jj", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  lookupEN(payload) {
    return request("/api/lookup/en", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  askGrammar(payload) {
    return request("/api/grammar/ask", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getGrammarThreads(bookId, chapterIndex) {
    return request(`/api/grammar/threads/${bookId}/${chapterIndex}`);
  },
  deleteGrammarThread(threadId) {
    return request(`/api/grammar/threads/${threadId}`, { method: "DELETE" });
  },
  getSettings() {
    return request("/api/settings");
  },
  updateSettings(payload) {
    return request("/api/settings", {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  getOllamaModels(url = "") {
    const qs = url ? `?url=${encodeURIComponent(url)}` : "";
    return request(`/api/llm/ollama-models${qs}`);
  },
  testLLM(payload = {}) {
    return request("/api/llm/test", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
};
