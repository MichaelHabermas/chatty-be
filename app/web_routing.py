"""Resolve when to run Tavily (explicit flags, heuristics, optional LLM router)."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Literal

from groq import AsyncGroq

from app.groq_chat import GROQ_CLIENT_EXCEPTIONS
from app.tavily_client import extract_last_user_text

WebSearchTriState = Literal["off", "on", "auto"]

_ROUTER_SYSTEM = (
    "You route user questions for a search assistant. Reply with JSON only, no markdown: "
    '{"need_web": <true|false>}. Set need_web to true if a good answer requires '
    "up-to-date facts from the web (news, live scores, prices, weather, current events, "
    "or anything that changes after your training cutoff). Set need_web to false for "
    "opinion, creative writing, code, math derivations, roleplay, or stable general knowledge."
)


def parse_web_search_header(value: str | None) -> WebSearchTriState | None:
    """Parse ``X-Chatty-Web-Search`` into ``on`` / ``off`` / ``auto``, or ``None`` if unset."""
    if value is None:
        return None
    s = value.strip()
    if not s:
        return None
    s = s.lower()
    if s in ("true", "1", "yes", "on"):
        return "on"
    if s in ("false", "0", "no", "off"):
        return "off"
    if s == "auto":
        return "auto"
    return None


def web_search_from_header(value: str | None) -> bool:
    """Legacy: true only for ``true`` / ``1`` / ``yes`` (case-insensitive)."""
    if value is None:
        return False
    s = value.strip().lower()
    return s in ("true", "1", "yes")


def _resolve_effective_mode(
    body_mode: WebSearchTriState | None,
    header: str | None,
) -> WebSearchTriState:
    """When ``web_search_mode`` is omitted and the header is not a tri-state value, default to ``auto``."""
    if body_mode is not None:
        return body_mode
    parsed = parse_web_search_header(header)
    if parsed is not None:
        return parsed
    return "auto"


def heuristic_web_search_signal(text: str) -> Literal["yes", "no", "maybe"]:
    """
    Fast, no-LLM signal: whether the last user text likely needs live web search.

    Returns ``yes`` / ``no`` / ``maybe`` (ambiguous → optional LLM router).
    """
    t = text.strip()
    if not t:
        return "no"

    lower = t.lower()

    # Strong no: short chitchat / thanks (prefix match keeps "hi there", "thanks!"-style turns)
    if len(t) <= 24 and re.match(r"^(hi|hello|hey|thanks|thank you|ok|okay|bye)\b", lower):
        return "no"

    # Strong no: code / implementation help without “look up” intent
    if "```" in t or "def " in lower or "import " in lower:
        if not re.search(
            r"\b(latest|document|docs|changelog|release notes|stackoverflow)\b", lower
        ):
            return "no"

    if re.search(
        r"\b(write|draft|compose)\b.*\b(poem|story|song|essay)\b", lower
    ) or re.search(r"\b(roleplay|pretend you are)\b", lower):
        return "no"

    # Strong yes
    if re.search(r"https?://", t):
        return "yes"
    if re.search(
        r"\b(latest|breaking|today|right now|current|this week|this month)\b", lower
    ):
        return "yes"
    if re.search(r"\b20[12][0-9]{2}\b", t):
        return "yes"
    if re.search(
        r"\b(news|headlines|weather|forecast|stock price|share price|earnings|ipo)\b",
        lower,
    ):
        return "yes"
    if re.search(r"\b(who won|final score|election results|standing(s)?)\b", lower):
        return "yes"
    if re.search(r"\b(according to (the )?news|what happened to)\b", lower):
        return "yes"

    return "maybe"


def web_search_router_model() -> str | None:
    """Model id for the optional LLM router, or ``None`` if unset (heuristic-only auto)."""
    m = os.environ.get("GROQ_WEB_SEARCH_ROUTER_MODEL", "").strip()
    return m or None


async def llm_needs_web_search(
    client: AsyncGroq,
    user_text: str,
    *,
    model: str,
) -> bool:
    """Ask a small Groq completion whether the user turn needs web search (JSON)."""
    try:
        completion = await client.chat.completions.create(
            model=model,
            temperature=0.0,
            max_tokens=80,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _ROUTER_SYSTEM},
                {"role": "user", "content": user_text[:4000]},
            ],
        )
    except GROQ_CLIENT_EXCEPTIONS:
        return False

    raw = ""
    if completion.choices:
        msg = completion.choices[0].message
        if msg and msg.content:
            raw = msg.content
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    val = data.get("need_web")
    return val is True


async def resolve_use_web_search(
    client: AsyncGroq,
    messages: list[dict[str, Any]],
    *,
    web_search_mode: WebSearchTriState | None,
    web_search: bool,
    header: str | None,
) -> bool:
    """
    Decide whether to call Tavily before Groq.

    * If ``web_search_mode`` is omitted and ``web_search`` is true and the header is not
      a tri-state value, treat as **on** (legacy OpenAI-style flag).
    * Else ``web_search_mode`` on the body wins; else the header tri-state; else default
      **auto** (server-side heuristics + optional router).
    * Mode ``auto`` uses heuristics, then optionally ``GROQ_WEB_SEARCH_ROUTER_MODEL`` for
      ``maybe`` (if unset, ``maybe`` defaults to **no** web search).
    """
    if (
        web_search_mode is None
        and web_search
        and parse_web_search_header(header) is None
    ):
        return True

    mode = _resolve_effective_mode(web_search_mode, header)
    if mode == "on":
        return True
    if mode == "off":
        return False

    # auto
    text = extract_last_user_text(messages)
    signal = heuristic_web_search_signal(text)
    if signal == "yes":
        return True
    if signal == "no":
        return False
    # maybe — router env unset ⇒ no Tavily (no extra Groq call)
    router_model = web_search_router_model()
    if router_model is None:
        return False
    return await llm_needs_web_search(client, text or "", model=router_model)
