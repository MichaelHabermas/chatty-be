"""Tests for optional completion webhook telemetry."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.completion_webhook import (
    build_completion_webhook_payload,
    completion_webhook_url,
    maybe_post_completion_webhook,
)


class TestBuildPayload(unittest.TestCase):
    def test_minimal(self):
        p = build_completion_webhook_payload(
            groq_request_id="rid",
            model="m",
            route="/chat",
            stream=False,
            latency_ms=12.3456,
            latency_kind="groq_round_trip",
            groq_ttfb_ms=None,
            web_sources=None,
            fallback_used=False,
        )
        self.assertEqual(p["event"], "chatty.completion")
        self.assertEqual(p["groq_request_id"], "rid")
        self.assertEqual(p["latency_kind"], "groq_round_trip")
        self.assertNotIn("groq_ttfb_ms", p)
        self.assertNotIn("web_sources_count", p)

    def test_stream_fields(self):
        p = build_completion_webhook_payload(
            groq_request_id=None,
            model="m",
            route="/v1/chat/completions",
            stream=True,
            latency_ms=100.0,
            latency_kind="stream_total",
            groq_ttfb_ms=5.5,
            web_sources=[{"url": "u"}],
            fallback_used=True,
        )
        self.assertEqual(p["groq_ttfb_ms"], 5.5)
        self.assertEqual(p["web_sources_count"], 1)


class TestMaybePost(unittest.IsolatedAsyncioTestCase):
    async def test_no_url_skips(self):
        http = MagicMock()
        http.post = AsyncMock()
        with patch("app.completion_webhook.completion_webhook_url", return_value=None):
            await maybe_post_completion_webhook(http, {"a": 1})
        http.post.assert_not_called()

    async def test_posts_when_configured(self):
        http = MagicMock()
        http.post = AsyncMock(return_value=MagicMock())
        with patch("app.completion_webhook.completion_webhook_url", return_value="http://hook.test/x"):
            await maybe_post_completion_webhook(http, {"event": "chatty.completion"})
        http.post.assert_called_once()
        call_kw = http.post.call_args
        self.assertEqual(call_kw[0][0], "http://hook.test/x")


class TestCompletionWebhookUrl(unittest.TestCase):
    def test_empty(self):
        with patch.dict("os.environ", {"CHATTY_COMPLETION_WEBHOOK_URL": ""}, clear=False):
            self.assertIsNone(completion_webhook_url())


if __name__ == "__main__":
    unittest.main()
