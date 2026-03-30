import os
from contextlib import asynccontextmanager

import groq
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DEFAULT_MODEL = "llama-3.3-70b-versatile"

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


def _model() -> str:
    m = os.environ.get("GROQ_MODEL", "").strip()
    return m or DEFAULT_MODEL


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.groq = groq.AsyncGroq(api_key=_require_api_key())
    yield
    await app.state.groq.close()


app = FastAPI(title="Chatty", lifespan=lifespan)


class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    prompt: str
    response: str


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest):
    client: groq.AsyncGroq = app.state.groq
    try:
        completion = await client.chat.completions.create(
            model=_model(),
            messages=[{"role": "user", "content": body.prompt}],
        )
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e

    return ChatResponse(prompt=body.prompt, response=_first_choice_content(completion))
