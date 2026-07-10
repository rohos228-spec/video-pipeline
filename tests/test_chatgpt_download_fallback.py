"""ChatGPT download: reply-text fallback and side-preview selectors."""

from __future__ import annotations

from app.bots.chatgpt import (
    FILE_PREVIEW_DOWNLOAD_BTN_MAX_PX,
    FILE_PREVIEW_DOWNLOAD_POLL_SEC,
    PLAIN_FILE_DOWNLOAD_POLL_SEC,
    FILE_PREVIEW_PANEL_SELECTORS,
    _PREVIEW_DOWNLOAD_FIND_JS,
    _PLAIN_FILE_DOWNLOAD_FIND_JS,
    _backend_file_url_variants,
    _response_looks_like_file,
    _uses_spreadsheet_preview,
    reply_text_usable_as_download,
)
from pathlib import Path


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


def test_file_preview_panel_selectors_are_narrow() -> None:
    joined = " ".join(FILE_PREVIEW_PANEL_SELECTORS)
    assert "aside:has" not in joined
    assert "Библиотека" not in joined


def test_plain_file_poll_shorter_than_xlsx() -> None:
    assert PLAIN_FILE_DOWNLOAD_POLL_SEC < FILE_PREVIEW_DOWNLOAD_POLL_SEC
    assert "plain-label" in _PLAIN_FILE_DOWNLOAD_FIND_JS


def test_backend_file_url_variants_simple_to_download() -> None:
    url = (
        "https://chatgpt.com/backend-api/files/file_abc/simple"
        "?conversation_id=x"
    )
    variants = _backend_file_url_variants(url)
    assert any("/download" in v for v in variants)
    assert any("/simple" not in v or "/download" in v for v in variants)


def test_preview_download_js_excludes_edit_buttons() -> None:
    assert "isEdit" in _PREVIEW_DOWNLOAD_FIND_JS
    assert "редактир" in _PREVIEW_DOWNLOAD_FIND_JS
