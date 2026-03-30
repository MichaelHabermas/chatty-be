import os
import time
from contextlib import asynccontextmanager

import groq
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from groq.types.chat import ChatCompletion
from pydantic import BaseModel, Field

from app.groq_chat import (
    OpenAIChatCompletionRequest,
    chat_completion_kwargs,
    default_model,
    groq_observability_headers,
    sse_stream_with_observability,
)

GROQ_HTTP_EXCEPTIONS = (
    groq.AuthenticationError,
    groq.PermissionDeniedError,
    groq.RateLimitError,
    groq.APIConnectionError,
    groq.APIStatusError,
)

def _require_api_key() -> str:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set")
    return key


def _groq_error_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, groq.APIStatusError):
        status = getattr(exc, "status_code", None) or 502
        detail = str(exc)
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                detail = resp.text or detail
            except httpx.HTTPError:
                pass
        code = status if 400 <= status < 600 else 502
        return HTTPException(status_code=code, detail=detail)
    if isinstance(exc, groq.APIConnectionError):
        return HTTPException(status_code=502, detail="Groq API unreachable")
    if isinstance(exc, groq.AuthenticationError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, groq.PermissionDeniedError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, groq.RateLimitError):
        return HTTPException(status_code=429, detail=str(exc))
    raise exc


def _first_choice_content(completion: ChatCompletion) -> str:
    if not completion.choices:
        return ""
    msg = completion.choices[0].message
    if msg is None:
        return ""
    return msg.content or ""


STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}


def _streaming_sse_response(obs: dict[str, str], sse_body):
    return StreamingResponse(
        sse_body,
        media_type="text/event-stream",
        headers={**STREAM_HEADERS, **obs},
    )


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    fastapi_app.state.groq = groq.AsyncGroq(api_key=_require_api_key())
    yield
    await fastapi_app.state.groq.close()


app = FastAPI(title="Chatty", lifespan=lifespan)


class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    stream: bool = False


class ChatResponse(BaseModel):
    prompt: str
    response: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat")
async def chat(body: ChatRequest):
    client: groq.AsyncGroq = app.state.groq
    if body.stream:
        v1_body = OpenAIChatCompletionRequest(
            messages=[{"role": "user", "content": body.prompt}],
            stream=True,
        )
        try:
            stream = await client.chat.completions.create(**chat_completion_kwargs(v1_body))
        except GROQ_HTTP_EXCEPTIONS as e:
            raise _groq_error_to_http(e) from e
        obs, sse_body = await sse_stream_with_observability(stream)
        return _streaming_sse_response(obs, sse_body)

    t0 = time.perf_counter()
    try:
        completion = await client.chat.completions.create(
            model=default_model(),
            messages=[{"role": "user", "content": body.prompt}],
        )
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e
    dur_ms = (time.perf_counter() - t0) * 1000.0
    obs = groq_observability_headers(duration_ms=dur_ms, request_id=completion.id)
    return JSONResponse(
        content=ChatResponse(
            prompt=body.prompt,
            response=_first_choice_content(completion),
        ).model_dump(),
        headers=obs,
    )


@app.post("/v1/chat/completions")
async def openai_chat_completions(body: OpenAIChatCompletionRequest):
    client: groq.AsyncGroq = app.state.groq
    kwargs = chat_completion_kwargs(body)
    t0 = time.perf_counter()
    try:
        result = await client.chat.completions.create(**kwargs)
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e

    if body.stream:
        obs, sse_body = await sse_stream_with_observability(result)
        return _streaming_sse_response(obs, sse_body)

    dur_ms = (time.perf_counter() - t0) * 1000.0
    obs = groq_observability_headers(duration_ms=dur_ms, request_id=result.id)
    return JSONResponse(
        content=result.model_dump(mode="json", exclude_none=True),
        headers=obs,
    )


@app.get("/v1/models")
async def openai_models():
    client: groq.AsyncGroq = app.state.groq
    try:
        listed = await client.models.list()
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e
    return JSONResponse(content=listed.model_dump(mode="json", exclude_none=True))
