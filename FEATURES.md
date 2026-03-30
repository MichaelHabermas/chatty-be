# Chatty — feature log

Short notes on what we add and why, so the proxy stays intentional as it grows.

## Optional Tavily web search (prefetch grounding)

### Added

- **`web_search`** on **`POST /chat`** and **`POST /v1/chat/completions`** (default false), plus **`X-Chatty-Web-Search`** header (`true` / `1` / `yes`) for clients that cannot add custom JSON fields (e.g. OpenAI SDK without `extra_body`).
- **`web_search_mode`**: **`off`** \| **`on`** \| **`auto`** — **omitted ⇒ `auto`** (server decides via heuristics + optional router). Explicit **`off`** / **`on`** overrides. Legacy **`web_search: true`** with **`web_search_mode`** omitted still forces Tavily on. **`auto`** uses **heuristics** on the last user message, with an optional **Groq JSON router** when **`GROQ_WEB_SEARCH_ROUTER_MODEL`** is set and the heuristic signal is ambiguous (unset router env ⇒ ambiguous **auto** resolves to no Tavily).
- Header tri-state: **`X-Chatty-Web-Search`**: **`auto`**, **`on`**, **`off`**, or legacy **`true`** / **`1`** / **`yes`** (and **`false`** / **`0`** / **`no`** for off).
- When enabled, **Tavily Search** runs first; results are injected as a **system** message, then Groq runs as usual (including streaming). Env: **`TAVILY_API_KEY`** (required when the resolved decision uses Tavily), optional **`TAVILY_MAX_RESULTS`**, **`TAVILY_SEARCH_DEPTH`**, optional **`GROQ_WEB_SEARCH_ROUTER_MODEL`**.
- **`web_sources` metadata** when Tavily actually ran: non-streaming JSON includes **`web_sources`** — a list of `{ "title", "url", "content" }` (snippet, same caps as injected context). Streaming (**`/chat`** and **`/v1/chat/completions`**) emits one SSE event first: **`event: chatty.web_sources`** with **`data: {"web_sources":[...]}`** (JSON), then the usual OpenAI-style **`data:`** completion chunks and **`data: [DONE]`**. Omitted when Tavily was not called (e.g. web off or no extractable user text).

### Why

Grounds the model on live web context without implementing a full tool-loop in Chatty; keeps the Groq path unchanged aside from augmented `messages`. **Auto** avoids requiring clients to guess when to set **`web_search`**, while keeping explicit control for tests and deterministic clients. Exposing **`web_sources`** lets UIs show citations and operators audit grounding without parsing assistant text.

### Docs

If the last user turn has no extractable text, Tavily is skipped. Queries are sent to Tavily when the feature is on; **auto** may send user text to Groq for routing when **`GROQ_WEB_SEARCH_ROUTER_MODEL`** is set; see [`CLAUDE.md`](CLAUDE.md).

---

## Agent-grade OpenAI compatibility (`/v1/chat/completions`)

### Added

Forwarding of `tools`, `tool_choice`, `parallel_tool_calls`, and `response_format` to Groq; `messages` are accepted as OpenAI-shaped JSON objects (`list` of dicts), not only `{role, content}` strings.

### Why

Clients using the official OpenAI SDK for **tool calling** and **JSON / structured output** send these fields and multi-turn messages (assistant `tool_calls`, tool results with `tool_call_id`). A strict `role`+`content`-only model dropped those fields and blocked agent flows. Passing dict messages and the extra kwargs keeps Chatty a true drop-in for Groq-backed agents without changing the server architecture.

### Docs

Request body still follows the subset documented in the app; unsupported fields should be omitted by clients or Groq may reject them.

---

## Response observability (Groq correlation + timing)

### Added

- **`Server-Timing`** — `groq;dur=…` (milliseconds) on **non-streaming** responses: wall time for the Groq `chat.completions.create` call.
- **`Server-Timing`** — `groq-ttfb;dur=…` on **SSE streaming** responses: time from starting the Groq request until the **first** chunk arrives (time-to-first-byte). Total stream duration is not in headers because the HTTP response starts before the stream ends.
- **`X-Groq-Request-Id`** — set from the completion **`id`** (non-stream) or the first chunk’s **`id`** (stream), matching Groq/OpenAI-style ids for support and log correlation.

Applied to **`POST /chat`** (JSON and stream) and **`POST /v1/chat/completions`** (JSON and stream). Not added to **`GET /v1/models`** or **`GET /health`**.

### Why

Gives callers a standard, stateless way to tie a Chatty HTTP response to Groq’s side of the work and to see latency without adding logging infrastructure. Streaming uses TTFB because response headers are sent before the body finishes.

### Docs

`Server-Timing` values are in **milliseconds** (`dur`). Browsers only expose `Server-Timing` to frontend JS if the response also includes `Timing-Allow-Origin` (not set here); for server-to-server and `curl -v` / proxies, headers are visible as usual.

---

## Rate-limit fallback model

### Added

- **`GROQ_FALLBACK_MODEL`** (optional env) — if the Groq call fails with **HTTP 429** (`RateLimitError` or `APIStatusError` with status 429), Chatty **retries once** with `model` set to this fallback. No retry if the env is unset, empty, or **equal to the request’s resolved primary model** (avoids loops).
- **`X-Chatty-Fallback-Used: 1`** on the HTTP response when the successful completion used the fallback model.

Applies to **`POST /chat`** and **`POST /v1/chat/completions`** (streaming and non-streaming).

### Why

Keeps traffic serving on a cheaper or higher-quota model when the primary is throttled, without queues or client-side retries. One retry only so failures stay visible if both models are limited.

### Docs

Set **`GROQ_FALLBACK_MODEL`** to a model id that differs from **`GROQ_MODEL`** / per-request `model`. Example: primary `llama-3.3-70b-versatile`, fallback `llama-3.1-8b-instant`. See `.env.example`.

---

## Optional bearer auth (Chatty-facing)

### Added

- **`CHATTY_API_KEY`** (optional env) — when set to a non-empty value, **`POST /chat`**, **`POST /v1/chat/completions`**, **`GET /v1/models`**, **`/openapi.json`**, **`/redoc`**, and **`/docs`** (including paths under **`/docs/`**, e.g. OAuth redirect) require the same **`Authorization: Bearer`** token (timing-safe comparison). **`GET /health`** stays unauthenticated for probes.

### Browser note

Swagger UI at **`/docs`** needs the Bearer on every request (including the first HTML load). Use a client that sends the header (e.g. `curl`, reverse proxy, or an extension), or open **`/docs`** after configuring your environment to attach **`Authorization`**.

### Why

Lets you expose Chatty on a network without leaving Groq-backed routes open while **`GROQ_API_KEY`** remains server-side. Matches how the OpenAI Python client sends `api_key` (Bearer), so `OpenAI(base_url=..., api_key=os.environ["CHATTY_API_KEY"])` works against Chatty.

### Docs

If **`CHATTY_API_KEY`** is unset or blank, behavior is unchanged (no Chatty-side auth). Generate a long random secret for production when enabled.
