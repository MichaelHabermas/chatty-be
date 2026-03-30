# Chatty — feature log

Short notes on what we add and why, so the proxy stays intentional as it grows.

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
