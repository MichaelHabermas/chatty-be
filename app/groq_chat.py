"""Shared Groq chat helpers: model resolution, create kwargs, SSE streaming."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

import groq
from groq import AsyncGroq, AsyncStream
from groq.types.chat import ChatCompletionChunk
from pydantic import BaseModel, Field

DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _sse_chunk_line(chunk: ChatCompletionChunk) -> str:
    return f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"


def default_model() -> str:
    m = os.environ.get("GROQ_MODEL", "").strip()
    return m or DEFAULT_MODEL


def resolve_model(request_model: str | None) -> str:
    if request_model and request_model.strip():
        return request_model.strip()
    return default_model()


def fallback_model() -> str | None:
    m = os.environ.get("GROQ_FALLBACK_MODEL", "").strip()
    return m or None


def with_fallback_header(headers: dict[str, str], used_fallback: bool) -> dict[str, str]:
    if not used_fallback:
        return headers
    return {**headers, "X-Chatty-Fallback-Used": "1"}


async def chat_completions_create_with_fallback(
    client: AsyncGroq,
    kwargs: dict[str, Any],
) -> tuple[Any, bool]:
    """On 429, retry once with ``GROQ_FALLBACK_MODEL`` if set and different from ``kwargs['model']``."""
    try:
        result = await client.chat.completions.create(**kwargs)
        return result, False
    except (groq.RateLimitError, groq.APIStatusError) as exc:
        # RateLimitError subclasses APIStatusError; only gate plain APIStatusError on 429.
        if isinstance(exc, groq.APIStatusError) and not isinstance(exc, groq.RateLimitError):
            if getattr(exc, "status_code", None) != 429:
                raise
        fb = fallback_model()
        primary = kwargs.get("model")
        if not fb or fb == primary:
            raise
        result = await client.chat.completions.create(**{**kwargs, "model": fb})
        return result, True


class OpenAIChatCompletionRequest(BaseModel):
    """OpenAI chat completions shape forwarded to Groq (agent-friendly subset)."""

    model: str | None = None
    messages: list[dict[str, Any]] = Field(..., min_length=1)
    stream: bool = False
    web_search: bool = False
    web_search_mode: Literal["off", "on", "auto"] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    top_p: float | None = None
    stop: str | list[str] | None = None
    user: str | None = Field(default=None, description="Stable user id for abuse detection")
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    parallel_tool_calls: bool | None = None
    response_format: dict[str, Any] | None = None


def chat_completion_kwargs(body: OpenAIChatCompletionRequest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": resolve_model(body.model),
        "messages": body.messages,
        "stream": body.stream,
    }
    if body.temperature is not None:
        kwargs["temperature"] = body.temperature
    if body.max_tokens is not None:
        kwargs["max_tokens"] = body.max_tokens
    if body.max_completion_tokens is not None:
        kwargs["max_completion_tokens"] = body.max_completion_tokens
    if body.top_p is not None:
        kwargs["top_p"] = body.top_p
    if body.stop is not None:
        kwargs["stop"] = body.stop
    if body.user is not None:
        kwargs["user"] = body.user
    if body.tools is not None:
        kwargs["tools"] = body.tools
    if body.tool_choice is not None:
        kwargs["tool_choice"] = body.tool_choice
    if body.parallel_tool_calls is not None:
        kwargs["parallel_tool_calls"] = body.parallel_tool_calls
    if body.response_format is not None:
        kwargs["response_format"] = body.response_format
    return kwargs


def groq_observability_headers(
    *,
    duration_ms: float,
    timing_name: str = "groq",
    request_id: str | None = None,
) -> dict[str, str]:
    """Server-Timing + Groq completion id for correlating with Groq support and logs."""
    headers: dict[str, str] = {
        "Server-Timing": f"{timing_name};dur={duration_ms:.2f}",
    }
    if request_id:
        headers["X-Groq-Request-Id"] = request_id
    return headers


async def sse_chat_completion_chunks(
    stream: AsyncStream[ChatCompletionChunk],
) -> AsyncIterator[str]:
    try:
        async for chunk in stream:
            yield _sse_chunk_line(chunk)
    finally:
        yield "data: [DONE]\n\n"


async def sse_stream_with_observability(
    stream: AsyncStream[ChatCompletionChunk],
) -> tuple[dict[str, str], AsyncIterator[str]]:
    """Peek the first SSE chunk so we can send TTFB + request id in response headers."""
    t0 = time.perf_counter()
    chunk_iter = stream.__aiter__()
    first = await chunk_iter.__anext__()
    ttfb_ms = (time.perf_counter() - t0) * 1000.0
    req_id = first.id or None
    obs = groq_observability_headers(
        duration_ms=ttfb_ms,
        timing_name="groq-ttfb",
        request_id=req_id,
    )

    async def _body() -> AsyncIterator[str]:
        try:
            yield _sse_chunk_line(first)
            async for chunk in chunk_iter:
                yield _sse_chunk_line(chunk)
        finally:
            yield "data: [DONE]\n\n"

    return obs, _body()
