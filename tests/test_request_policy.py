"""Tests for declarative request policy (deny, redact, prepend)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.request_policy import RequestPolicy, apply_request_policy, load_request_policy


class TestApplyPolicy(unittest.TestCase):
    def test_empty_policy_identity(self):
        p = RequestPolicy()
        msgs = [{"role": "user", "content": "hello"}]
        out = apply_request_policy(msgs, p)
        self.assertIs(out, msgs)

    def test_prepend(self):
        p = RequestPolicy(prepend_system="PREFIX")
        out = apply_request_policy([{"role": "user", "content": "hi"}], p)
        self.assertEqual(out[0]["role"], "system")
        self.assertTrue(str(out[0]["content"]).startswith("PREFIX"))

    def test_deny(self):
        p = RequestPolicy(deny_patterns=[__import__("re").compile(r"SECRET")])
        with self.assertRaises(HTTPException) as ctx:
            apply_request_policy([{"role": "user", "content": "my SECRET key"}], p)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_redact_then_readable(self):
        p = RequestPolicy(
            redact_specs=[(__import__("re").compile(r"\d{3}-\d{2}-\d{4}"), "[SSN]")]
        )
        out = apply_request_policy([{"role": "user", "content": "id 123-45-6789"}], p)
        self.assertIn("[SSN]", out[0]["content"])
        self.assertNotIn("123-45-6789", out[0]["content"])

    def test_order_deny_before_redact(self):
        """Deny matches raw text; blocked content is not redacted away first."""
        p = RequestPolicy(
            deny_patterns=[__import__("re").compile(r"BLOCKME")],
            redact_specs=[(__import__("re").compile(r"BLOCKME"), "redacted")],
        )
        with self.assertRaises(HTTPException):
            apply_request_policy([{"role": "user", "content": "BLOCKME"}], p)


class TestLoadPolicy(unittest.TestCase):
    def test_env_only_prepend(self):
        with patch.dict(
            "os.environ",
            {"CHATTY_REQUEST_POLICY": "", "CHATTY_PREPEND_SYSTEM": "E1"},
            clear=False,
        ):
            p = load_request_policy()
        self.assertEqual(p.prepend_system, "E1")

    def test_file_and_env_merge(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"prepend_system": "F", "deny_message_patterns": [r"XXX"]}, f)
            f.flush()
            path = f.name
        try:
            with patch.dict(
                "os.environ",
                {
                    "CHATTY_REQUEST_POLICY": path,
                    "CHATTY_PREPEND_SYSTEM": "E",
                    "CHATTY_DENY_MESSAGE_PATTERN": r"YYY",
                },
                clear=False,
            ):
                p = load_request_policy()
            self.assertEqual(p.prepend_system, "F\n\nE")
            self.assertEqual(len(p.deny_patterns), 2)
        finally:
            Path(path).unlink(missing_ok=True)

    def test_invalid_file_raises(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{")
            f.flush()
            path = f.name
        try:
            with patch.dict("os.environ", {"CHATTY_REQUEST_POLICY": path}, clear=False):
                with self.assertRaises(RuntimeError):
                    load_request_policy()
        finally:
            Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
