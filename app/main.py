import os
from contextlib import asynccontextmanager

import groq
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.groq_chat import (
    ChatMessageBody,
    OpenAIChatCompletionRequest,
    chat_completion_kwargs,
    default_model,
    sse_chat_completion_chunks,
)

GROQ_HTTP_EXCEPTIONS = (
    groq.AuthenticationError,
    groq.PermissionDeniedError,
    groq.RateLimitError,
    groq.APIConnectionError,
    groq.APIStatusError,
)

_GROQ_SIMPLE_STATUS: dict[type[Exception], int] = {
    groq.AuthenticationError: 401,
    groq.PermissionDeniedError: 403,
    groq.RateLimitError: 429,
}


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
            except Exception:
                pass
        code = status if 400 <= status < 600 else 502
        return HTTPException(status_code=code, detail=detail)
    if isinstance(exc, groq.APIConnectionError):
        return HTTPException(status_code=502, detail="Groq API unreachable")
    for exc_type, code in _GROQ_SIMPLE_STATUS.items():
        if isinstance(exc, exc_type):
            return HTTPException(status_code=code, detail=str(exc))
    raise exc


def _first_choice_content(completion: object) -> str:
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
            messages=[ChatMessageBody(role="user", content=body.prompt)],
            stream=True,
        )
        try:
            stream = await client.chat.completions.create(**chat_completion_kwargs(v1_body))
        except GROQ_HTTP_EXCEPTIONS as e:
            raise _groq_error_to_http(e) from e
        return StreamingResponse(
            sse_chat_completion_chunks(stream),
            media_type="text/event-stream",
            headers=STREAM_HEADERS,
        )

    try:
        completion = await client.chat.completions.create(
            model=default_model(),
            messages=[{"role": "user", "content": body.prompt}],
        )
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e

    return ChatResponse(prompt=body.prompt, response=_first_choice_content(completion))


@app.post("/v1/chat/completions")
async def openai_chat_completions(body: OpenAIChatCompletionRequest):
    client: groq.AsyncGroq = app.state.groq
    kwargs = chat_completion_kwargs(body)
    try:
        result = await client.chat.completions.create(**kwargs)
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e

    if body.stream:
        return StreamingResponse(
            sse_chat_completion_chunks(result),
            media_type="text/event-stream",
            headers=STREAM_HEADERS,
        )

    return JSONResponse(content=result.model_dump(mode="json", exclude_none=True))


@app.get("/v1/models")
async def openai_models():
    client: groq.AsyncGroq = app.state.groq
    try:
        listed = await client.models.list()
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e
    return JSONResponse(content=listed.model_dump(mode="json", exclude_none=True))
