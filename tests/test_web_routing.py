"""Tests for web search mode resolution and heuristics."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.web_routing import (
    heuristic_web_search_signal,
    parse_web_search_header,
    resolve_use_web_search,
    web_search_from_header,
)


class TestParseHeader(unittest.TestCase):
    def test_tri_state(self):
        self.assertEqual(parse_web_search_header("auto"), "auto")
        self.assertEqual(parse_web_search_header("OFF"), "off")
        self.assertEqual(parse_web_search_header("true"), "on")
        self.assertEqual(parse_web_search_header("1"), "on")
        self.assertEqual(parse_web_search_header("no"), "off")

    def test_unknown_returns_none(self):
        self.assertIsNone(parse_web_search_header("maybe"))
        self.assertIsNone(parse_web_search_header(None))
        self.assertIsNone(parse_web_search_header("   "))


class TestLegacyHeader(unittest.TestCase):
    def test_only_true_yes_one(self):
        self.assertTrue(web_search_from_header("true"))
        self.assertTrue(web_search_from_header("YES"))
        self.assertFalse(web_search_from_header("auto"))
        self.assertFalse(web_search_from_header("false"))


class TestHeuristic(unittest.TestCase):
    def test_yes_signals(self):
        self.assertEqual(heuristic_web_search_signal("What is the latest news on AI?"), "yes")
        self.assertEqual(heuristic_web_search_signal("Check https://example.com for docs"), "yes")
        self.assertEqual(heuristic_web_search_signal("Who won the game last night?"), "yes")

    def test_no_signals(self):
        self.assertEqual(heuristic_web_search_signal("hi"), "no")
        self.assertEqual(heuristic_web_search_signal("thanks"), "no")
        self.assertEqual(heuristic_web_search_signal("def foo():\n    pass\n```"), "no")

    def test_maybe(self):
        self.assertEqual(heuristic_web_search_signal("Explain quantum entanglement briefly."), "maybe")


class TestResolveUseWebSearch(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = MagicMock()

    async def test_explicit_on_off(self):
        msgs = [{"role": "user", "content": "hello"}]
        self.assertTrue(
            await resolve_use_web_search(
                self.client, msgs, web_search_mode="on", web_search=False, header=None
            )
        )
        self.assertFalse(
            await resolve_use_web_search(
                self.client, msgs, web_search_mode="off", web_search=True, header=None
            )
        )

    async def test_legacy_bool(self):
        msgs = [{"role": "user", "content": "hello"}]
        self.assertTrue(
            await resolve_use_web_search(
                self.client, msgs, web_search_mode=None, web_search=True, header=None
            )
        )

    async def test_header_auto(self):
        msgs = [{"role": "user", "content": "latest news about space"}]
        self.assertTrue(
            await resolve_use_web_search(
                self.client, msgs, web_search_mode=None, web_search=False, header="auto"
            )
        )

    async def test_default_auto_uses_heuristic_without_explicit_mode(self):
        """Omitted web_search_mode defaults to auto (not legacy off-only)."""
        msgs = [{"role": "user", "content": "What is the latest news on AI?"}]
        self.assertTrue(
            await resolve_use_web_search(
                self.client, msgs, web_search_mode=None, web_search=False, header=None
            )
        )

    async def test_auto_maybe_without_router_env(self):
        msgs = [{"role": "user", "content": "Explain the philosophy of stoicism."}]
        with patch("app.web_routing.web_search_router_model", return_value=None):
            r = await resolve_use_web_search(
                self.client, msgs, web_search_mode="auto", web_search=False, header=None
            )
        self.assertFalse(r)

    async def test_auto_maybe_with_router_calls_llm(self):
        self.client.chat = MagicMock()
        self.client.chat.completions = MagicMock()
        self.client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(content='{"need_web": true}')
                    )
                ]
            )
        )
        msgs = [{"role": "user", "content": "Explain the philosophy of stoicism."}]
        with patch.dict(
            "os.environ",
            {"GROQ_WEB_SEARCH_ROUTER_MODEL": "llama-3.1-8b-instant"},
        ):
            r = await resolve_use_web_search(
                self.client, msgs, web_search_mode="auto", web_search=False, header=None
            )
        self.assertTrue(r)
        self.client.chat.completions.create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
