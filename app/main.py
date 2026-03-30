import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import time
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import groq
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from groq.types.chat import ChatCompletion
from pydantic import BaseModel, Field

from app.groq_chat import (
    OpenAIChatCompletionRequest,
    chat_completion_kwargs,
    chat_completions_create_with_fallback,
    default_model,
    groq_observability_headers,
    sse_stream_with_observability,
    with_fallback_header,
)
from app.tavily_client import augment_messages_with_web
from app.web_routing import resolve_use_web_search

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


http_bearer = HTTPBearer(auto_error=False)

_CHATTY_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing bearer token",
    headers={"WWW-Authenticate": 'Bearer realm="chatty"'},
)


def _verify_chatty_bearer_authorization_header(authorization: str | None) -> None:
    """When ``CHATTY_API_KEY`` is set, require a matching ``Authorization: Bearer`` value."""
    expected = os.environ.get("CHATTY_API_KEY", "").strip()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise _CHATTY_UNAUTHORIZED
    token = authorization.removeprefix("Bearer ").strip()
    if len(token) != len(expected):
        raise _CHATTY_UNAUTHORIZED
    if not secrets.compare_digest(token, expected):
        raise _CHATTY_UNAUTHORIZED


async def require_chatty_bearer(
    cred: Annotated[HTTPAuthorizationCredentials | None, Depends(http_bearer)],
) -> None:
    """When ``CHATTY_API_KEY`` is set, require ``Authorization: Bearer <token>`` (OpenAI SDK compatible)."""
    if cred is None:
        _verify_chatty_bearer_authorization_header(None)
        return
    _verify_chatty_bearer_authorization_header(f"Bearer {cred.credentials}")


def _docs_paths_require_chatty_auth(path: str) -> bool:
    if path in ("/openapi.json", "/redoc"):
        return True
    return path.startswith("/docs")


class ChattyDocsAuthMiddleware(BaseHTTPMiddleware):
    """When ``CHATTY_API_KEY`` is set, protect OpenAPI + Swagger/ReDoc the same as API routes."""

    async def dispatch(self, request: Request, call_next):
        if not _docs_paths_require_chatty_auth(request.url.path):
            return await call_next(request)
        try:
            _verify_chatty_bearer_authorization_header(request.headers.get("Authorization"))
        except HTTPException as e:
            hdrs = dict(e.headers) if e.headers else {}
            return JSONResponse(
                status_code=e.status_code,
                content={"detail": e.detail},
                headers=hdrs,
            )
        return await call_next(request)


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
    fastapi_app.state.http = httpx.AsyncClient(timeout=60.0)
    yield
    await fastapi_app.state.http.aclose()
    await fastapi_app.state.groq.close()


app = FastAPI(title="Chatty", lifespan=lifespan)
app.add_middleware(ChattyDocsAuthMiddleware)


class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    stream: bool = False
    web_search: bool = False
    web_search_mode: Literal["off", "on", "auto"] | None = None


class ChatResponse(BaseModel):
    prompt: str
    response: str
    web_sources: list[dict[str, str]] | None = None


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/chat", dependencies=[Depends(require_chatty_bearer)])
async def chat(
    body: ChatRequest,
    x_chatty_web_search: str | None = Header(None, alias="X-Chatty-Web-Search"),
):
    client: groq.AsyncGroq = app.state.groq
    http: httpx.AsyncClient = app.state.http
    base_messages = [{"role": "user", "content": body.prompt}]
    use_web = await resolve_use_web_search(
        client,
        base_messages,
        web_search_mode=body.web_search_mode,
        web_search=body.web_search,
        header=x_chatty_web_search,
    )
    messages, web_sources = await augment_messages_with_web(
        http,
        base_messages,
        web_search=use_web,
    )
    if body.stream:
        v1_body = OpenAIChatCompletionRequest(
            messages=messages,
            stream=True,
        )
        try:
            kwargs = chat_completion_kwargs(v1_body)
            stream, used_fb = await chat_completions_create_with_fallback(client, kwargs)
        except GROQ_HTTP_EXCEPTIONS as e:
            raise _groq_error_to_http(e) from e
        obs, sse_body = await sse_stream_with_observability(
            stream,
            web_sources=web_sources,
        )
        obs = with_fallback_header(obs, used_fb)
        return _streaming_sse_response(obs, sse_body)

    t0 = time.perf_counter()
    try:
        completion, used_fb = await chat_completions_create_with_fallback(
            client,
            {
                "model": default_model(),
                "messages": messages,
            },
        )
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e
    dur_ms = (time.perf_counter() - t0) * 1000.0
    obs = with_fallback_header(
        groq_observability_headers(duration_ms=dur_ms, request_id=completion.id),
        used_fb,
    )
    return JSONResponse(
        content=ChatResponse(
            prompt=body.prompt,
            response=_first_choice_content(completion),
            web_sources=web_sources,
        ).model_dump(exclude_none=True),
        headers=obs,
    )


@app.post("/v1/chat/completions", dependencies=[Depends(require_chatty_bearer)])
async def openai_chat_completions(
    body: OpenAIChatCompletionRequest,
    x_chatty_web_search: str | None = Header(None, alias="X-Chatty-Web-Search"),
):
    client: groq.AsyncGroq = app.state.groq
    http: httpx.AsyncClient = app.state.http
    use_web = await resolve_use_web_search(
        client,
        list(body.messages),
        web_search_mode=body.web_search_mode,
        web_search=body.web_search,
        header=x_chatty_web_search,
    )
    augmented, web_sources = await augment_messages_with_web(
        http,
        list(body.messages),
        web_search=use_web,
    )
    kwargs = chat_completion_kwargs(body.model_copy(update={"messages": augmented}))
    t0 = time.perf_counter()
    try:
        result, used_fb = await chat_completions_create_with_fallback(client, kwargs)
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e

    if body.stream:
        obs, sse_body = await sse_stream_with_observability(
            result,
            web_sources=web_sources,
        )
        obs = with_fallback_header(obs, used_fb)
        return _streaming_sse_response(obs, sse_body)

    dur_ms = (time.perf_counter() - t0) * 1000.0
    obs = with_fallback_header(
        groq_observability_headers(duration_ms=dur_ms, request_id=result.id),
        used_fb,
    )
    out = result.model_dump(mode="json", exclude_none=True)
    if web_sources is not None:
        out["web_sources"] = web_sources
    return JSONResponse(
        content=out,
        headers=obs,
    )


@app.get("/v1/models", dependencies=[Depends(require_chatty_bearer)])
async def openai_models():
    client: groq.AsyncGroq = app.state.groq
    try:
        listed = await client.models.list()
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e
    return JSONResponse(content=listed.model_dump(mode="json", exclude_none=True))
