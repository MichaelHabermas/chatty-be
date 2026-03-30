"""Tests for Tavily web_sources metadata returned alongside completions."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx

from app.groq_chat import _sse_web_sources_event
from app.tavily_client import augment_messages_with_web, tavily_results_to_web_sources


class TestTavilyResultsToWebSources(unittest.TestCase):
    def test_maps_and_truncates(self):
        raw = [
            {
                "title": "T" * 300,
                "url": "https://a.example",
                "content": "C" * 900,
            }
        ]
        out = tavily_results_to_web_sources(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["title"]), 200)
        self.assertEqual(len(out[0]["content"]), 800)
        self.assertEqual(out[0]["url"], "https://a.example")

    def test_skips_non_dicts(self):
        self.assertEqual(
            tavily_results_to_web_sources([None, "x", {}]),
            [{"title": "", "url": "", "content": ""}],
        )


class TestAugmentMessagesWithWeb(unittest.IsolatedAsyncioTestCase):
    async def test_web_off_no_sources(self):
        msgs = [{"role": "user", "content": "hi"}]
        http = httpx.AsyncClient()
        out, src = await augment_messages_with_web(http, msgs, web_search=False)
        self.assertEqual(out, msgs)
        self.assertIsNone(src)

    async def test_no_user_text_no_tavily(self):
        msgs = [{"role": "assistant", "content": "only"}]
        http = httpx.AsyncClient()
        out, src = await augment_messages_with_web(http, msgs, web_search=True)
        self.assertEqual(out, msgs)
        self.assertIsNone(src)

    async def test_tavily_returns_sources(self):
        msgs = [{"role": "user", "content": "latest news"}]
        fake = {
            "results": [
                {"title": "Hi", "url": "https://x.test", "content": "snippet"},
            ]
        }

        async def fake_search(_http, *, query: str):
            return fake

        http = httpx.AsyncClient()
        with patch("app.tavily_client.tavily_search", side_effect=fake_search):
            out, src = await augment_messages_with_web(http, msgs, web_search=True)
        self.assertIsNotNone(src)
        assert src is not None
        self.assertEqual(len(src), 1)
        self.assertEqual(src[0]["url"], "https://x.test")
        self.assertEqual(src[0]["title"], "Hi")
        self.assertEqual(src[0]["content"], "snippet")
        self.assertEqual(out[0]["role"], "system")


class TestSseWebSourcesEvent(unittest.TestCase):
    def test_event_shape(self):
        line = _sse_web_sources_event([{"title": "a", "url": "u", "content": "c"}])
        self.assertTrue(line.startswith("event: chatty.web_sources\n"))
        self.assertIn("data: ", line)
        payload = line.split("data: ", 1)[1].strip()
        data = json.loads(payload)
        self.assertEqual(data["web_sources"][0]["url"], "u")


if __name__ == "__main__":
    unittest.main()
