# CLAUDE.md

Guidance for working in this repository.

## Quick start

Copy `.env.example` to `.env` and set `GROQ_API_KEY` from https://console.groq.com/keys

**Local:**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**Docker:** `docker compose up`

Server: `http://localhost:8000` — API docs at `/docs`.

## How it works

FastAPI app in `app/main.py` wrapping the Groq API. Lifespan creates an `AsyncGroq` client, stores it on `app.state`, and closes it on shutdown.

- **`POST /chat`** — User prompt → Groq (default model `llama-3.3-70b-versatile`, override with `GROQ_MODEL`). Request/response via Pydantic (`ChatRequest`, `ChatResponse`).
- **`GET /health`** — Health check.
- **Errors** — Map Groq exceptions (e.g. auth, rate limit) to HTTP status codes (401, 403, 429, 502); re-raise unexpected errors.

## Configuration

From `.env.example`:

- `GROQ_API_KEY` (required) — Groq console
- `GROQ_MODEL` (optional) — defaults to `llama-3.3-70b-versatile`

## Dependencies

Pinned in `requirements.txt`: FastAPI, Uvicorn, Groq.
