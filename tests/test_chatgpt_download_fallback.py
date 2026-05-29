"""ChatGPT download: reply-text fallback for voiceover .txt."""

from __future__ import annotations

from app.bots.chatgpt import reply_text_usable_as_download


def test_reply_text_usable_min_length() -> None:
    assert reply_text_usable_as_download("x" * 10)
    assert not reply_text_usable_as_download("short")
    assert not reply_text_usable_as_download("   ")
