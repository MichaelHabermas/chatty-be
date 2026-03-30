# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Quick Start

**Setup**: Copy `.env.example` to `.env` and set `GROQ_API_KEY` from https://console.groq.com/keys

**Local development**:
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**Docker**:
```bash
docker compose up
```

Server runs on `http://localhost:8000` with API docs at `/docs` (Swagger UI).

## Architecture

Single-file FastAPI service (`app/main.py`) that wraps the Groq LLM API:

- **Lifespan context manager**: Initializes `AsyncGroq` client on startup, closes on shutdown
- **`POST /chat`**: Main endpoint—takes a user prompt, calls Groq with `llama-3.3-70b-versatile` (configurable), returns structured response
- **`GET /health`**: Simple health check
- **Error mapping**: Groq exceptions (`AuthenticationError`, `RateLimitError`, etc.) mapped to appropriate HTTP status codes (401, 403, 429, 502)

## Configuration

Environment variables (see `.env.example`):
- `GROQ_API_KEY` (required): API key from Groq console
- `GROQ_MODEL` (optional): Model ID, defaults to `llama-3.3-70b-versatile`

## Dependencies

- **fastapi** ≥0.115.0: Web framework
- **uvicorn[standard]** ≥0.32.0: ASGI server
- **groq** ≥0.15.0: Groq API client (async support)

All pinned in `requirements.txt`.

## Key Patterns

**Request/Response Models**: Use Pydantic (`ChatRequest`, `ChatResponse`) for validation and serialization.

**Async-first**: All endpoints and Groq client calls are async.

**Error handling**: Catch specific Groq exceptions and convert to HTTP errors; re-raise unknown exceptions.

**Client lifecycle**: Groq client stored in `app.state` during lifespan, ensures proper async cleanup.

---

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