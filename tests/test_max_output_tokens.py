"""Tests for CHATTY_MAX_OUTPUT_TOKENS clamping."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.groq_chat import (
    OpenAIChatCompletionRequest,
    apply_output_token_cap,
    chat_completion_kwargs,
    max_output_tokens_ceiling,
)


class TestMaxOutputTokensCeiling(unittest.TestCase):
    def test_unset(self):
        with patch.dict("os.environ", {"CHATTY_MAX_OUTPUT_TOKENS": ""}, clear=False):
            self.assertIsNone(max_output_tokens_ceiling())

    def test_positive(self):
        with patch.dict("os.environ", {"CHATTY_MAX_OUTPUT_TOKENS": "1024"}, clear=False):
            self.assertEqual(max_output_tokens_ceiling(), 1024)

    def test_invalid(self):
        with patch.dict("os.environ", {"CHATTY_MAX_OUTPUT_TOKENS": "nope"}, clear=False):
            self.assertIsNone(max_output_tokens_ceiling())


class TestApplyOutputTokenCap(unittest.TestCase):
    def test_no_cap_noop(self):
        kw = {"max_tokens": 5000}
        with patch.dict("os.environ", {"CHATTY_MAX_OUTPUT_TOKENS": ""}, clear=False):
            apply_output_token_cap(kw)
        self.assertEqual(kw["max_tokens"], 5000)

    def test_clamps_both(self):
        kw = {"max_tokens": 5000, "max_completion_tokens": 9999}
        with patch.dict("os.environ", {"CHATTY_MAX_OUTPUT_TOKENS": "1000"}, clear=False):
            apply_output_token_cap(kw)
        self.assertEqual(kw["max_tokens"], 1000)
        self.assertEqual(kw["max_completion_tokens"], 1000)

    def test_respects_lower_client_value(self):
        kw = {"max_tokens": 100}
        with patch.dict("os.environ", {"CHATTY_MAX_OUTPUT_TOKENS": "1000"}, clear=False):
            apply_output_token_cap(kw)
        self.assertEqual(kw["max_tokens"], 100)


class TestChatCompletionKwargsCap(unittest.TestCase):
    def test_integrated(self):
        body = OpenAIChatCompletionRequest(
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5000,
            stream=False,
        )
        with patch.dict("os.environ", {"CHATTY_MAX_OUTPUT_TOKENS": "512"}, clear=False):
            kw = chat_completion_kwargs(body)
        self.assertEqual(kw["max_tokens"], 512)


if __name__ == "__main__":
    unittest.main()
