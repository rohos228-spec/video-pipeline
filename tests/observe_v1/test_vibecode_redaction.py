import json
import unittest

from app.services.observation import digest, redact


class VibecodeKeyRedaction(unittest.TestCase):
    TOKEN = "vk-AbCdEf0123456789xyz"

    def test_vk_token_in_text_is_redacted(self):
        text = redact(f"HTTP 401 for key {self.TOKEN} on model gpt-5.6-sol")
        self.assertNotIn(self.TOKEN, text)
        self.assertIn("gpt-5.6-sol", text)

    def test_vk_token_in_nested_body_is_redacted(self):
        body = {"messages": [{"role": "user", "content": f"use {self.TOKEN} please"}],
                "error": {"message": f"bad key: {self.TOKEN}"}}
        self.assertNotIn(self.TOKEN, json.dumps(redact(body)))

    def test_sk_token_still_redacted(self):
        self.assertNotIn("sk-ABCDEFGH12345678", redact("key sk-ABCDEFGH12345678"))

    def test_short_vk_prefix_in_ordinary_words_is_kept(self):
        self.assertEqual(redact("see vk-test and vk-1"), "see vk-test and vk-1")

    def test_digest_ignores_the_token_value(self):
        self.assertEqual(digest({"t": f"k {self.TOKEN}"}), digest({"t": "k vk-ZZZZZZZZZZZZZZZZ"}))
