"""Shared Groq chat helpers: model resolution, create kwargs, SSE streaming."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

from groq import AsyncStream
from groq.types.chat import ChatCompletionChunk
from pydantic import BaseModel, Field

DEFAULT_MODEL = "llama-3.3-70b-versatile"


def default_model() -> str:
    m = os.environ.get("GROQ_MODEL", "").strip()
    return m or DEFAULT_MODEL


def resolve_model(request_model: str | None) -> str:
    if request_model and request_model.strip():
        return request_model.strip()
    return default_model()


class ChatMessageBody(BaseModel):
    role: str
    content: str


class OpenAIChatCompletionRequest(BaseModel):
    """Subset of OpenAI chat completions for Groq."""

    model: str | None = None
    messages: list[ChatMessageBody]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    top_p: float | None = None
    stop: str | list[str] | None = None
    user: str | None = Field(default=None, description="Stable user id for abuse detection")


def chat_completion_kwargs(body: OpenAIChatCompletionRequest) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": resolve_model(body.model),
        "messages": [m.model_dump() for m in body.messages],
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
    return kwargs


async def sse_chat_completion_chunks(
    stream: AsyncStream[ChatCompletionChunk],
) -> AsyncIterator[str]:
    try:
        async for chunk in stream:
            yield f"data: {chunk.model_dump_json(exclude_none=True)}\n\n"
    finally:
        yield "data: [DONE]\n\n"
