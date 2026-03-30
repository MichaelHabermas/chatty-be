"""Optional fire-and-forget POST to CHATTY_COMPLETION_WEBHOOK_URL after successful completions."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def completion_webhook_url() -> str | None:
    u = os.environ.get("CHATTY_COMPLETION_WEBHOOK_URL", "").strip()
    return u or None


def _webhook_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    bearer = os.environ.get("CHATTY_WEBHOOK_BEARER", "").strip()
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    return headers


def build_completion_webhook_payload(
    *,
    groq_request_id: str | None,
    model: str,
    route: str,
    stream: bool,
    latency_ms: float,
    latency_kind: str,
    groq_ttfb_ms: float | None,
    web_sources: list[Any] | None,
    fallback_used: bool,
) -> dict[str, Any]:
    """Stable JSON shape for operators / SIEM (no raw message bodies)."""
    payload: dict[str, Any] = {
        "event": "chatty.completion",
        "groq_request_id": groq_request_id or "",
        "model": model,
        "stream": stream,
        "route": route,
        "latency_ms": round(latency_ms, 3),
        "latency_kind": latency_kind,
        "fallback_used": fallback_used,
    }
    if groq_ttfb_ms is not None:
        payload["groq_ttfb_ms"] = round(groq_ttfb_ms, 3)
    if web_sources is not None:
        payload["web_sources_count"] = len(web_sources)
    return payload


async def maybe_post_completion_webhook(
    http: httpx.AsyncClient,
    payload: dict[str, Any],
) -> None:
    url = completion_webhook_url()
    if not url:
        return
    try:
        await http.post(url, json=payload, headers=_webhook_headers(), timeout=5.0)
    except (httpx.TimeoutException, httpx.RequestError, OSError) as exc:
        logger.debug("Completion webhook failed: %s", exc)


async def wrap_sse_with_completion_webhook(
    sse_body: AsyncIterator[str],
    http: httpx.AsyncClient,
    *,
    groq_request_id: str | None,
    model: str,
    route: str,
    groq_ttfb_ms: float,
    web_sources: list[Any] | None,
    used_fallback: bool,
) -> AsyncIterator[str]:
    """Yield the same SSE stream; after the body finishes, POST stream_total telemetry."""
    t0 = time.perf_counter()
    try:
        async for chunk in sse_body:
            yield chunk
    finally:
        total_ms = (time.perf_counter() - t0) * 1000.0
        if completion_webhook_url():
            payload = build_completion_webhook_payload(
                groq_request_id=groq_request_id,
                model=model,
                route=route,
                stream=True,
                latency_ms=total_ms,
                latency_kind="stream_total",
                groq_ttfb_ms=groq_ttfb_ms,
                web_sources=web_sources,
                fallback_used=used_fallback,
            )

            async def _send() -> None:
                await maybe_post_completion_webhook(http, payload)

            asyncio.create_task(_send())
