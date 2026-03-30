"""Optional declarative transforms before Groq: deny, redact, prepend system."""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Pattern

from fastapi import HTTPException, status


@dataclass
class RequestPolicy:
    """Loaded policy; empty fields mean no-op for that step."""

    prepend_system: str | None = None
    deny_patterns: list[Pattern[str]] = field(default_factory=list)
    redact_specs: list[tuple[Pattern[str], str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            not (self.prepend_system and self.prepend_system.strip())
            and not self.deny_patterns
            and not self.redact_specs
        )


def _compile_patterns(
    raw: list[str],
    *,
    label: str,
) -> list[Pattern[str]]:
    out: list[Pattern[str]] = []
    for i, s in enumerate(raw):
        try:
            out.append(re.compile(s))
        except re.error as e:
            raise RuntimeError(f"{label}[{i}] invalid regex: {e}") from e
    return out


def load_request_policy() -> RequestPolicy:
    """
    Load policy from ``CHATTY_REQUEST_POLICY`` (JSON file) and optional env shortcuts.

    Env (merged with file):
    * ``CHATTY_PREPEND_SYSTEM`` — appended after file ``prepend_system`` if both set.
    * ``CHATTY_DENY_MESSAGE_PATTERN`` — one extra deny regex (combined with file list).
    """
    prepend_parts: list[str] = []
    deny_raw: list[str] = []
    redact_raw: list[tuple[str, str]] = []

    path = os.environ.get("CHATTY_REQUEST_POLICY", "").strip()
    if path:
        p = Path(path)
        if not p.is_file():
            raise RuntimeError(f"CHATTY_REQUEST_POLICY={path!r} is not a file")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"CHATTY_REQUEST_POLICY invalid JSON: {e}") from e
        if not isinstance(data, dict):
            raise RuntimeError("CHATTY_REQUEST_POLICY JSON must be an object")
        ps = data.get("prepend_system")
        if isinstance(ps, str) and ps.strip():
            prepend_parts.append(ps.strip())
        dm = data.get("deny_message_patterns")
        if dm is not None:
            if not isinstance(dm, list):
                raise RuntimeError("deny_message_patterns must be a list of strings")
            for j, item in enumerate(dm):
                if not isinstance(item, str):
                    raise RuntimeError(f"deny_message_patterns[{j}] must be a string")
                if item.strip():
                    deny_raw.append(item)
        rp = data.get("redact_patterns")
        if rp is not None:
            if not isinstance(rp, list):
                raise RuntimeError("redact_patterns must be a list")
            for j, item in enumerate(rp):
                if not isinstance(item, dict):
                    raise RuntimeError(f"redact_patterns[{j}] must be an object")
                pat = item.get("pattern")
                repl = item.get("replacement", "")
                if not isinstance(pat, str) or not isinstance(repl, str):
                    raise RuntimeError(
                        f"redact_patterns[{j}] needs string pattern and replacement"
                    )
                redact_raw.append((pat, repl))

    env_prepend = os.environ.get("CHATTY_PREPEND_SYSTEM", "").strip()
    if env_prepend:
        prepend_parts.append(env_prepend)

    env_deny = os.environ.get("CHATTY_DENY_MESSAGE_PATTERN", "").strip()
    if env_deny:
        deny_raw.append(env_deny)

    prepend_system = "\n\n".join(prepend_parts) if prepend_parts else None
    deny_patterns = _compile_patterns(deny_raw, label="deny_message_patterns")
    redact_specs: list[tuple[Pattern[str], str]] = []
    for j, (pat_s, repl) in enumerate(redact_raw):
        try:
            redact_specs.append((re.compile(pat_s), repl))
        except re.error as e:
            raise RuntimeError(f"redact_patterns[{j}] invalid regex: {e}") from e

    return RequestPolicy(
        prepend_system=prepend_system,
        deny_patterns=deny_patterns,
        redact_specs=redact_specs,
    )


def _collect_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []

    def take(s: str) -> None:
        if s:
            parts.append(s)

    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            take(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    t = part.get("text")
                    if isinstance(t, str):
                        take(t)
    return "\n".join(parts)


def _redact_string(s: str, specs: list[tuple[Pattern[str], str]]) -> str:
    out = s
    for pat, repl in specs:
        out = pat.sub(repl, out)
    return out


def _apply_redact_to_messages(messages: list[dict[str, Any]], specs: list[tuple[Pattern[str], str]]) -> None:
    if not specs:
        return
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = _redact_string(content, specs)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    t = part.get("text")
                    if isinstance(t, str):
                        part["text"] = _redact_string(t, specs)


def _merge_prepend_system(messages: list[dict[str, Any]], fragment: str) -> None:
    frag = fragment.strip()
    if not frag:
        return
    if messages and messages[0].get("role") == "system":
        existing = messages[0].get("content")
        if isinstance(existing, str):
            messages[0] = {**messages[0], "content": frag + "\n\n" + existing}
        else:
            messages.insert(0, {"role": "system", "content": frag})
    else:
        messages.insert(0, {"role": "system", "content": frag})


def apply_request_policy(messages: list[dict[str, Any]], policy: RequestPolicy) -> list[dict[str, Any]]:
    """
    Order: **deny** (on raw message text) → **redact** → **prepend system**.

    Raises ``HTTPException(400)`` if a deny pattern matches.
    """
    if policy.is_empty():
        return messages

    out = copy.deepcopy(messages)
    blob = _collect_text(out)
    for pat in policy.deny_patterns:
        if pat.search(blob):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Request blocked by Chatty request policy",
            )
    _apply_redact_to_messages(out, policy.redact_specs)
    if policy.prepend_system:
        _merge_prepend_system(out, policy.prepend_system)
    return out
