# Chatty

A small [FastAPI](https://fastapi.tiangolo.com/) service that proxies [Groq](https://groq.com/) chat completions. It exposes **POST /chat**, **OpenAI-compatible** routes under **/v1**, and optional **SSE streaming**. Your Groq API key stays on the server.

Optional features: [Tavily](https://www.tavily.com/) web search (with **auto** / **on** / **off** modes), declarative **request policy** (deny / redact / prepend system), **completion webhook** telemetry, **rate-limit fallback** model, optional **CHATTY_API_KEY** bearer auth for exposed deployments, and response **observability** headers (`Server-Timing`, `X-Groq-Request-Id`). See [CLAUDE.md](CLAUDE.md) for behavior details and [FEATURES.md](FEATURES.md) for a feature changelog.

## Quick start

1. Copy `.env.example` to `.env` and set `GROQ_API_KEY` from the [Groq console](https://console.groq.com/keys). For optional web search, set `TAVILY_API_KEY` from [Tavily](https://www.tavily.com/).
2. Run locally:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Or with Docker:

```bash
docker compose up
```

- **Server:** [http://localhost:8000](http://localhost:8000)
- **Interactive docs:** [http://localhost:8000/docs](http://localhost:8000/docs)

## Configuration

Required and common optional variables (see `.env.example` for the full list):

| Variable | Notes |
| --- | --- |
| `GROQ_API_KEY` | **Required** — [Groq console](https://console.groq.com/keys) |
| `GROQ_MODEL` | Optional — defaults to `llama-3.3-70b-versatile` |
| `GROQ_FALLBACK_MODEL` | Optional — on HTTP 429, retry once with this model (must differ from primary) |
| `GROQ_WEB_SEARCH_ROUTER_MODEL` | Optional — small Groq model for **auto** web-search routing when heuristics are ambiguous |
| `TAVILY_API_KEY` | Optional — required when the resolved decision runs Tavily |
| `TAVILY_MAX_RESULTS`, `TAVILY_SEARCH_DEPTH` | Optional Tavily tuning |
| `CHATTY_API_KEY` | Optional — require `Authorization: Bearer` on chat, `/v1`, and docs (not `/health`) |
| `CHATTY_REQUEST_POLICY` | Optional — JSON path for deny / redact / prepend rules |
| `CHATTY_PREPEND_SYSTEM`, `CHATTY_DENY_MESSAGE_PATTERN` | Optional policy shortcuts |
| `CHATTY_COMPLETION_WEBHOOK_URL`, `CHATTY_WEBHOOK_BEARER` | Optional post-completion telemetry POST |
| `CHATTY_MAX_OUTPUT_TOKENS` | Optional — caps `max_tokens` / `max_completion_tokens` when clients send them |

## API overview

- **POST /chat** — JSON: `prompt` (required), optional `stream`, `web_search`, `web_search_mode` (`off` \| `on` \| `auto`; omitted defaults to **auto**). Returns `prompt` + `response`, or SSE when `stream` is true. Header **X-Chatty-Web-Search** can force or tune web search when JSON fields are awkward for your client.
- **POST /v1/chat/completions** — OpenAI-style: `messages`, optional `model`, `stream`, `temperature`, `max_tokens`, `max_completion_tokens`, `top_p`, `stop`, `user`, `web_search`, `web_search_mode`. Forwards `tools`, `tool_choice`, `parallel_tool_calls`, and `response_format` to Groq. JSON or SSE.
- **GET /v1/models** — Groq model list (OpenAI-compatible shape).
- **GET /health** — Liveness check (unauthenticated even when `CHATTY_API_KEY` is set).

Successful chat responses may include **Server-Timing** (`groq` or `groq-ttfb` for streams) and **X-Groq-Request-Id** for correlation. When Tavily runs, responses include **web_sources** (non-stream JSON) or an initial SSE **event: chatty.web_sources** (streaming).

Groq errors are mapped to HTTP status codes (e.g. 401, 403, 429, 502) where applicable. If web search is required but `TAVILY_API_KEY` is missing, the server returns **503**.

## OpenAI Python client

Point the official OpenAI SDK at this server so calls go through Chatty (Groq credentials remain env-only). If you set `CHATTY_API_KEY`, pass it as `api_key` so requests include `Authorization: Bearer`.

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="not-used",  # or os.environ["CHATTY_API_KEY"] when Chatty auth is enabled
)

response = client.chat.completions.create(
    model="llama-3.3-70b-versatile",
    messages=[{"role": "user", "content": "Hello"}],
)
```

Use `stream=True` for SSE streaming.

## Project layout

- `app/main.py` — FastAPI app, routes, auth, Groq error handling
- `app/groq_chat.py` — Request mapping, SSE, observability headers, fallback model
- `app/web_routing.py` — Web search mode resolution (heuristics + optional router)
- `app/tavily_client.py` — Tavily search and message augmentation
- `app/request_policy.py` — Declarative deny / redact / prepend
- `app/completion_webhook.py` — Optional completion telemetry POST

## Dependencies

See [requirements.txt](requirements.txt): FastAPI, Uvicorn, Groq, httpx, python-dotenv.
