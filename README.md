# Shiori — Interactive Japanese EPUB Reader

A local web app for learning Japanese by reading EPUB novels with `<ruby>`-aware word lookups, monolingual-first explanations, context-aware grammar Q&A, trouble tracking, and zero-backlog mini-games.

## Features

- **EPUB Reader (`FastAPI` + `SudachiPy`):** Scans `.epub` files in `books/`, groups logical chapters, streams embedded covers and illustrations, and preserves `<ruby>` furigana inside clickable word tokens.
- **Japanese-Friendly Layouts:** Toggle between horizontal (`horizontal-tb`) and vertical (`vertical-rl`) reading, switch themes (`Paper`, `Sepia`, `Dark`), adjust font size, and cycle furigana visibility (`All`, `Trouble`, `Hover`, `Off`).
- **Two-Step Dictionary Lookup:**
  1. **Monolingual First (J-J):** Generates a learner-friendly Japanese definition, contextual nuance, and everyday example sentences (cached permanently in SQLite) using local **Ollama** or **Google Gemini API**.
  2. **Optional English Fallback:** Instant offline **JMdict** lookup via `Jamdict`, logging an implicit difficulty signal.
- **Inline Grammar Assistant:** Click `?` on any sentence or highlight a phrase to open a context-aware Q&A drawer with persistent chapter notes.
- **Trouble Radar & Active Reading Timer:** Automatically tracks words and grammar points you struggle with, paired with an idle-aware session timer.
- **Zero-Backlog Mini-Games:** Chapter-end or timer-triggered 5-question rounds (Context Cloze, Bunsetsu Scramble, Particle Fix) that retire mastered items instead of building flashcard backlogs.

## Setup

1. Install Python dependencies with [uv](https://docs.astral.sh/uv/):

   ```powershell
   uv sync
   ```

2. Download and unpack the offline JMdict SQLite database into `data/jamdict.db`:

   ```powershell
   uv run python scripts/setup_dict.py
   ```

3. Place your DRM-free Japanese `.epub` files inside `books/`.

4. Configure an AI provider (optional — offline JMdict lookups and reading work without an LLM):
   - **Ollama (Local):** Start Ollama and pull a Japanese-capable model such as `ollama pull qwen3:8b` or `ollama pull gemma3:4b`.
   - **Gemini API:** Open **Settings** inside the app and enter a Google AI Studio API key (or set `GEMINI_API_KEY`).

## Run

```powershell
uv run python run.py
```

Open `http://127.0.0.1:8000` in your browser.

## Tests

```powershell
uv run pytest -v
```
