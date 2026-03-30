"""FastAPI application entrypoint for Chatty.

Groq-backed chat, OpenAI-compatible routes, optional Tavily web search.
"""

import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import groq
import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from groq.types.chat import ChatCompletion
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.completion_webhook import (
    build_completion_webhook_payload,
    maybe_post_completion_webhook,
    wrap_sse_with_completion_webhook,
)
from app.groq_chat import (
    OpenAIChatCompletionRequest,
    apply_output_token_cap,
    chat_completion_kwargs,
    chat_completions_create_with_fallback,
    default_model,
    groq_observability_headers,
    resolve_model,
    sse_stream_with_observability,
    with_fallback_header,
)
from app.request_policy import apply_request_policy, load_request_policy
from app.tavily_client import augment_messages_with_web
from app.web_routing import resolve_use_web_search

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

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
    """Require ``Authorization: Bearer`` when ``CHATTY_API_KEY`` is set (OpenAI SDK compatible)."""
    if cred is None:
        _verify_chatty_bearer_authorization_header(None)
        return
    _verify_chatty_bearer_authorization_header(f"Bearer {cred.credentials}")


def _docs_paths_require_chatty_auth(path: str) -> bool:
    if path in ("/openapi.json", "/redoc"):
        return True
    return path.startswith("/docs")


class ChattyDocsAuthMiddleware(BaseHTTPMiddleware):  # pylint: disable=too-few-public-methods
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
        http_status = getattr(exc, "status_code", None) or 502
        detail = str(exc)
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                detail = resp.text or detail
            except httpx.HTTPError:
                pass
        code = http_status if 400 <= http_status < 600 else 502
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
    """Create and tear down shared Groq client, HTTP client, and request policy."""
    fastapi_app.state.groq = groq.AsyncGroq(api_key=_require_api_key())
    fastapi_app.state.http = httpx.AsyncClient(timeout=60.0)
    fastapi_app.state.request_policy = load_request_policy()
    yield
    await fastapi_app.state.http.aclose()
    await fastapi_app.state.groq.close()


app = FastAPI(title="Chatty", lifespan=lifespan)
app.add_middleware(ChattyDocsAuthMiddleware)


class ChatRequest(BaseModel):
    """Body for ``POST /chat``."""

    prompt: str = Field(..., min_length=1)
    stream: bool = False
    web_search: bool = False
    web_search_mode: Literal["off", "on", "auto"] | None = None


class ChatResponse(BaseModel):
    """Non-streaming JSON response for ``POST /chat``."""

    prompt: str
    response: str
    web_sources: list[dict[str, str]] | None = None


@app.get("/health")
async def health():
    """Liveness probe."""
    return {"status": "ok"}


@app.post("/chat", dependencies=[Depends(require_chatty_bearer)])
async def chat(  # pylint: disable=too-many-locals
    body: ChatRequest,
    background_tasks: BackgroundTasks,
    x_chatty_web_search: str | None = Header(None, alias="X-Chatty-Web-Search"),
):
    """Simple chat endpoint: optional web search, then Groq (stream or JSON)."""
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
    messages = apply_request_policy(messages, app.state.request_policy)
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
        obs, sse_body, ttfb_ms = await sse_stream_with_observability(
            stream,
            web_sources=web_sources,
        )
        obs = with_fallback_header(obs, used_fb)
        sse_body = wrap_sse_with_completion_webhook(
            sse_body,
            http,
            groq_request_id=obs.get("X-Groq-Request-Id"),
            model=default_model(),
            route="/chat",
            groq_ttfb_ms=ttfb_ms,
            web_sources=web_sources,
            used_fallback=used_fb,
        )
        return _streaming_sse_response(obs, sse_body)

    t0 = time.perf_counter()
    chat_kw: dict[str, Any] = {"model": default_model(), "messages": messages}
    apply_output_token_cap(chat_kw)
    try:
        completion, used_fb = await chat_completions_create_with_fallback(
            client,
            chat_kw,
        )
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e
    dur_ms = (time.perf_counter() - t0) * 1000.0
    obs = with_fallback_header(
        groq_observability_headers(duration_ms=dur_ms, request_id=completion.id),
        used_fb,
    )
    background_tasks.add_task(
        maybe_post_completion_webhook,
        http,
        build_completion_webhook_payload(
            groq_request_id=completion.id,
            model=default_model(),
            route="/chat",
            stream=False,
            latency_ms=dur_ms,
            latency_kind="groq_round_trip",
            groq_ttfb_ms=None,
            web_sources=web_sources,
            fallback_used=used_fb,
        ),
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
async def openai_chat_completions(  # pylint: disable=too-many-locals
    body: OpenAIChatCompletionRequest,
    background_tasks: BackgroundTasks,
    x_chatty_web_search: str | None = Header(None, alias="X-Chatty-Web-Search"),
):
    """OpenAI-compatible chat completions (subset of fields), with optional web search."""
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
    augmented = apply_request_policy(augmented, app.state.request_policy)
    kwargs = chat_completion_kwargs(body.model_copy(update={"messages": augmented}))
    resolved_model = resolve_model(body.model)
    t0 = time.perf_counter()
    try:
        result, used_fb = await chat_completions_create_with_fallback(client, kwargs)
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e

    if body.stream:
        obs, sse_body, ttfb_ms = await sse_stream_with_observability(
            result,
            web_sources=web_sources,
        )
        obs = with_fallback_header(obs, used_fb)
        sse_body = wrap_sse_with_completion_webhook(
            sse_body,
            http,
            groq_request_id=obs.get("X-Groq-Request-Id"),
            model=resolved_model,
            route="/v1/chat/completions",
            groq_ttfb_ms=ttfb_ms,
            web_sources=web_sources,
            used_fallback=used_fb,
        )
        return _streaming_sse_response(obs, sse_body)

    dur_ms = (time.perf_counter() - t0) * 1000.0
    obs = with_fallback_header(
        groq_observability_headers(duration_ms=dur_ms, request_id=result.id),
        used_fb,
    )
    background_tasks.add_task(
        maybe_post_completion_webhook,
        http,
        build_completion_webhook_payload(
            groq_request_id=result.id,
            model=resolved_model,
            route="/v1/chat/completions",
            stream=False,
            latency_ms=dur_ms,
            latency_kind="groq_round_trip",
            groq_ttfb_ms=None,
            web_sources=web_sources,
            fallback_used=used_fb,
        ),
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
    """List models from Groq (OpenAI-compatible)."""
    client: groq.AsyncGroq = app.state.groq
    try:
        listed = await client.models.list()
    except GROQ_HTTP_EXCEPTIONS as e:
        raise _groq_error_to_http(e) from e
    return JSONResponse(content=listed.model_dump(mode="json", exclude_none=True))
