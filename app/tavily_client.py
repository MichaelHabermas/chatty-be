"""Tavily Search API: query env, HTTP client call, message augmentation."""

from __future__ import annotations

import copy
import os
from typing import Any

import httpx
from fastapi import HTTPException, status

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_MAX_SNIPPET_CHARS = 800


def tavily_max_results() -> int:
    raw = os.environ.get("TAVILY_MAX_RESULTS", "").strip()
    if not raw:
        return 5
    try:
        return max(1, min(20, int(raw)))
    except ValueError:
        return 5


def tavily_search_depth() -> str:
    d = os.environ.get("TAVILY_SEARCH_DEPTH", "basic").strip().lower()
    allowed = frozenset({"basic", "advanced", "fast", "ultra-fast"})
    return d if d in allowed else "basic"


def extract_last_user_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            return " ".join(parts).strip()
    return ""


def _format_web_context(results: list[dict[str, Any]]) -> str:
    lines = [
        "The following are web search results. Use them to ground your answer and cite URLs when relevant.",
        "",
    ]
    for i, r in enumerate(results, 1):
        title = str(r.get("title") or "")[:200]
        url = str(r.get("url") or "")[:500]
        snippet = str(r.get("content") or "")[:_MAX_SNIPPET_CHARS]
        lines.append(f"{i}. {title}\n   URL: {url}\n   {snippet}\n")
    return "\n".join(lines)


def inject_web_context(
    messages: list[dict[str, Any]],
    context_text: str,
) -> list[dict[str, Any]]:
    out = copy.deepcopy(messages)
    if not context_text.strip():
        return out
    augmented = "Below is context from a web search (Tavily).\n\n" + context_text
    if out and out[0].get("role") == "system":
        existing = out[0].get("content")
        if isinstance(existing, str):
            out[0] = {**out[0], "content": augmented + "\n\n" + existing}
        else:
            out.insert(0, {"role": "system", "content": augmented})
    else:
        out.insert(0, {"role": "system", "content": augmented})
    return out


def _tavily_http_error(resp: httpx.Response) -> HTTPException:
    detail = resp.text
    try:
        data = resp.json()
        if isinstance(data, dict):
            inner = data.get("detail")
            if isinstance(inner, dict) and "error" in inner:
                detail = str(inner["error"])
            elif isinstance(inner, str):
                detail = inner
    except (ValueError, TypeError):
        pass
    code = resp.status_code
    if code == 401:
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)
    if code == 429:
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)
    if code in (432, 433):
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=detail)
    if code == 400:
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    if code >= 500:
        return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)


async def tavily_search(http: httpx.AsyncClient, *, query: str) -> dict[str, Any]:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TAVILY_API_KEY is not set but web search was requested",
        )
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": tavily_max_results(),
        "search_depth": tavily_search_depth(),
    }
    try:
        resp = await http.post(TAVILY_SEARCH_URL, json=payload)
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Tavily API request timed out",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Tavily API unreachable: {exc!s}",
        ) from exc

    if resp.status_code != 200:
        raise _tavily_http_error(resp)
    try:
        return resp.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Tavily returned invalid JSON",
        ) from exc


async def augment_messages_with_web(
    http: httpx.AsyncClient,
    messages: list[dict[str, Any]],
    *,
    web_search: bool,
) -> list[dict[str, Any]]:
    if not web_search:
        return messages
    query = extract_last_user_text(messages)
    if not query:
        return messages
    data = await tavily_search(http, query=query)
    results = data.get("results")
    if not isinstance(results, list):
        results = []
    ctx = _format_web_context([r for r in results if isinstance(r, dict)])
    return inject_web_context(messages, ctx)


def web_search_from_header(value: str | None) -> bool:
    if value is None or not value.strip():
        return False
    return value.strip().lower() in ("true", "1", "yes")
