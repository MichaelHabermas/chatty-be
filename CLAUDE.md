# CLAUDE.md

Guidance for working in this repository.

## Quick start

Copy `.env.example` to `.env` and set `GROQ_API_KEY` from [Groq console](https://console.groq.com/keys)

**Local:**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**Docker:** `docker compose up`

Server: [http://localhost:8000](http://localhost:8000) — API docs at `/docs`.

## How it works

FastAPI app in `app/main.py` wrapping the Groq API. Lifespan creates an `AsyncGroq` client, stores it on `app.state`, and closes it on shutdown. Shared request mapping and SSE streaming live in `app/groq_chat.py`.

- **`POST /chat`** — Simple prompt → Groq (default model `llama-3.3-70b-versatile`, override with `GROQ_MODEL`). JSON body: `prompt` (required), `stream` (optional, default false). If `stream` is false, response is `ChatResponse` (`prompt`, `response`). If `stream` is true, the response is **SSE** (`text/event-stream`) in the same shape as OpenAI streaming (`data: {...}` lines, then `data: [DONE]`).
- **`POST /v1/chat/completions`** — OpenAI-compatible chat completions (subset of fields): `messages` (required), optional `model`, `stream`, `temperature`, `max_tokens`, `max_completion_tokens`, `top_p`, `stop`, `user`. Non-streaming returns JSON matching Groq’s `ChatCompletion` shape; streaming returns SSE as above.
- **`GET /v1/models`** — Lists models from Groq (OpenAI-compatible list response).
- **`GET /health`** — Health check.
- **Errors** — Map Groq exceptions (e.g. auth, rate limit) to HTTP status codes (401, 403, 429, 502); re-raise unexpected errors.

### OpenAI Python client (drop-in base URL)

Point the official OpenAI SDK at this server so `chat.completions` calls go to Groq via Chatty (the real `GROQ_API_KEY` stays server-side only):

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-used",  # required by the SDK; not sent to Groq
)
r = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": "Hello"}],
)
```

Use `stream=True` for SSE streaming; the client will consume the same `data:` / `[DONE]` stream Chatty emits.

## Configuration

From `.env.example`:

- `GROQ_API_KEY` (required) — Groq console
- `GROQ_MODEL` (optional) — defaults to `llama-3.3-70b-versatile`

## Dependencies

Pinned in `requirements.txt`: FastAPI, Uvicorn, Groq.
