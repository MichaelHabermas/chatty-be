# CLAUDE.md

Guidance for working in this repository.

## Quick start

Copy `.env.example` to `.env` and set `GROQ_API_KEY` from [Groq console](https://console.groq.com/keys). For optional web search, set `TAVILY_API_KEY` from [Tavily](https://www.tavily.com/).

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

FastAPI app in `app/main.py` wrapping the Groq API. Lifespan creates an `AsyncGroq` client and an `httpx.AsyncClient` for Tavily, stores them on `app.state`, and closes them on shutdown. Shared request mapping and SSE streaming live in `app/groq_chat.py`. Tavily helpers live in `app/tavily_client.py`.

- **`POST /chat`** — Simple prompt → Groq (default model `llama-3.3-70b-versatile`, override with `GROQ_MODEL`). JSON body: `prompt` (required), `stream` (optional, default false), `web_search` (optional, default false). If `stream` is false, response is `ChatResponse` (`prompt`, `response`). If `stream` is true, the response is **SSE** (`text/event-stream`) in the same shape as OpenAI streaming (`data: {...}` lines, then `data: [DONE]`).
- **`POST /v1/chat/completions`** — OpenAI-compatible chat completions (subset of fields): `messages` (required), optional `model`, `stream`, `temperature`, `max_tokens`, `max_completion_tokens`, `top_p`, `stop`, `user`, `web_search`. Non-streaming returns JSON matching Groq’s `ChatCompletion` shape; streaming returns SSE as above.

### Optional web search (Tavily)

When **`web_search` is true** on the JSON body **or** the header **`X-Chatty-Web-Search: true`** (also `1` / `yes`, case-insensitive) is sent, Chatty calls **Tavily Search** first using the last user message text as the query, injects a **system** message with summarized results, then calls Groq. **Latency** is Tavily + Groq sequentially. **`TAVILY_API_KEY`** must be set when web search is requested; otherwise the server returns **503**. If web search is on but no user text can be extracted (e.g. empty content), Chatty skips Tavily and calls Groq only. **Privacy:** enabling web search sends the derived query to Tavily’s API; see [Tavily](https://www.tavily.com/).

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
- `TAVILY_API_KEY` (optional) — required only when `web_search` / `X-Chatty-Web-Search` is used
- `TAVILY_MAX_RESULTS` (optional) — default 5, capped at 20
- `TAVILY_SEARCH_DEPTH` (optional) — `basic`, `advanced`, `fast`, or `ultra-fast` (default `basic`)

## Dependencies

Pinned in `requirements.txt`: FastAPI, Uvicorn, Groq, httpx.

---

## Self-Improve

Before starting a new task, review existing rules and hypotheses for this domain.

Apply rules by default. Check if any hypothesis can be tested with today's work.

At the end of each task, extract insights.
Store them in domain folders, e.g.:

/knowledge/pricing/
  knowledge.md (facts and patterns)
  hypotheses.md (need more data)
  rules.md (confirmed — apply by default)

Maintain a /knowledge/INDEX.md that routes to each domain folder.

When a hypothesis gets confirmed 5+ times, promote it to a rule.

When a rule gets contradicted by new data, demote it back to a hypothesis.
