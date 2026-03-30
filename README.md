# Chatty

A small [FastAPI](https://fastapi.tiangolo.com/) service that proxies [Groq](https://groq.com/) chat completions. It exposes a simple `**POST /chat**` API, **OpenAI-compatible** routes under `**/v1`**, and optional **SSE streaming**. Your Groq API key stays on the server.

## Quick start

1. Copy `.env.example` to `.env` and set `GROQ_API_KEY` from the [Groq console](https://console.groq.com/keys).
2. Run locally:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r v
uvicorn app.main:app --reload
```

Or with Docker:

```bash
docker compose up
```

- **Server:** [http://localhost:8000](http://localhost:8000)
- **Interactive docs:** [http://localhost:8000/docs](http://localhost:8000/docs)

## Configuration

- `**GROQ_API_KEY`** (required) — from the [Groq console](https://console.groq.com/keys)
- `**GROQ_MODEL**` (optional) — defaults to `llama-3.3-70b-versatile`

## API overview

- `**POST /chat**` — JSON body: `prompt`, optional `stream`. Returns `prompt` + `response`, or SSE when `stream` is true.
- `**POST /v1/chat/completions**` — OpenAI-style chat completions (`messages`, optional `model`, `stream`, `temperature`, `max_tokens`, `max_completion_tokens`, `top_p`, `stop`, `user`). JSON or SSE.
- `**GET /v1/models**` — Groq model list (OpenAI-compatible shape).
- `**GET /health**` — Liveness check.

Groq errors are mapped to HTTP status codes (e.g. 401, 403, 429, 502) where applicable.

## OpenAI Python client

Point the official OpenAI SDK at this server so calls go through Chatty (Groq credentials remain env-only):

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-used",  # required by the SDK; not sent to Groq
)

response = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": "Hello"}],
)
```

Use `stream=True` for SSE streaming.

## Project layout

- `app/main.py` — FastAPI app, routes, Groq error handling
- `app/groq_chat.py` — Request mapping and SSE helpers

## Dependencies

See `[requirements.txt](requirements.txt)`: FastAPI, Uvicorn, Groq.