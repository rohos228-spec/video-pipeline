"""ChatGPT download: reply-text fallback and side-preview selectors."""

from __future__ import annotations

from app.bots.chatgpt import (
    FILE_PREVIEW_DOWNLOAD_BTN_MAX_PX,
    FILE_PREVIEW_DOWNLOAD_POLL_SEC,
    FILE_PREVIEW_PANEL_SELECTORS,
    _PREVIEW_DOWNLOAD_FIND_JS,
    _PREVIEW_TOOLBAR_VISIBLE_JS,
    _response_looks_like_file,
    reply_text_usable_as_download,
)


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


def test_preview_download_js_uses_global_right_toolbar() -> None:
    assert "data-vp-preview-download" in _PREVIEW_DOWNLOAD_FIND_JS
    assert "vw * 0.40" in _PREVIEW_DOWNLOAD_FIND_JS
    assert "global-penultimate" in _PREVIEW_DOWNLOAD_FIND_JS
    assert "inChat" in _PREVIEW_DOWNLOAD_FIND_JS
    assert FILE_PREVIEW_DOWNLOAD_BTN_MAX_PX <= 64
    assert FILE_PREVIEW_DOWNLOAD_POLL_SEC >= 20


def test_preview_toolbar_visible_js_checks_zoom_on_right() -> None:
    assert "100%" in _PREVIEW_TOOLBAR_VISIBLE_JS or "%" in _PREVIEW_TOOLBAR_VISIBLE_JS
    assert "vw * 0.38" in _PREVIEW_TOOLBAR_VISIBLE_JS
