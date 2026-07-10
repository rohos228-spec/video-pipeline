"""ChatGPT download: reply-text fallback for voiceover .txt."""

from __future__ import annotations

from app.bots.chatgpt import _response_looks_like_file, reply_text_usable_as_download


class _FakeResp:
    def __init__(self, url: str, content_type: str = "", ok: bool = True) -> None:
        self.url = url
        self.ok = ok
        self.headers = {"content-type": content_type}


def test_response_looks_like_file_xlsx_url() -> None:
    assert _response_looks_like_file(
        _FakeResp("https://chatgpt.com/backend-api/files/abc/download")
    )


def test_response_looks_like_file_octet_stream() -> None:
    assert _response_looks_like_file(
        _FakeResp("https://x.com/dl", "application/octet-stream")
    )


def test_reply_text_usable_min_length() -> None:
    assert reply_text_usable_as_download("x" * 10)
    assert not reply_text_usable_as_download("short")
    assert not reply_text_usable_as_download("   ")
