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
  listManga() {
    return request("/api/manga");
  },
  scanManga() {
    return request("/api/manga/scan", { method: "POST" });
  },
  getManga(bookId) {
    return request(`/api/manga/${bookId}`);
  },
  ocrMangaPage(bookId, pageIndex, force = false) {
    return request(`/api/manga/${bookId}/pages/${pageIndex}/ocr`, {
      method: "POST",
      body: JSON.stringify({ force: Boolean(force) }),
    });
  },
  ocrMangaCrop(bookId, pageIndex, coords) {
    return request(`/api/manga/${bookId}/pages/${pageIndex}/ocr-crop`, {
      method: "POST",
      body: JSON.stringify(coords),
    });
  },
  tokenizeMangaText(text) {
    return request("/api/manga/tokenize", {
      method: "POST",
      body: JSON.stringify({ text }),
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
  getLlamaCppModels(url = "") {
    const qs = url ? `?url=${encodeURIComponent(url)}` : "";
    return request(`/api/llm/llamacpp-models${qs}`);
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
  getTrouble({ bookId = null, includeRetired = false, limit = 80 } = {}) {
    const params = new URLSearchParams();
    if (bookId != null) params.set("book_id", String(bookId));
    if (includeRetired) params.set("include_retired", "true");
    params.set("limit", String(limit));
    return request(`/api/trouble?${params.toString()}`);
  },
  setTroubleRetired(itemId, retired) {
    return request(`/api/trouble/${itemId}/retire`, {
      method: "POST",
      body: JSON.stringify({ retired }),
    });
  },
  generateQuiz(payload) {
    return request("/api/quiz/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  submitQuiz(payload) {
    return request("/api/quiz/submit", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  getQuizHistory(bookId = null, limit = 15) {
    const params = new URLSearchParams();
    if (bookId != null) params.set("book_id", String(bookId));
    params.set("limit", String(limit));
    return request(`/api/quiz/history?${params.toString()}`);
  },
  listNotes({ bookId = null, chapterIndex = null, category = "", limit = 200 } = {}) {
    const params = new URLSearchParams();
    if (bookId != null) params.set("book_id", String(bookId));
    if (chapterIndex != null) params.set("chapter_index", String(chapterIndex));
    if (category) params.set("category", category);
    params.set("limit", String(limit));
    return request(`/api/notes?${params.toString()}`);
  },
  createNote(payload) {
    return request("/api/notes", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },
  updateNote(noteId, payload) {
    return request(`/api/notes/${noteId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    });
  },
  deleteNote(noteId) {
    return request(`/api/notes/${noteId}`, { method: "DELETE" });
  },
};
