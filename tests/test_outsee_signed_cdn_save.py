"""Репро: thumb→full без подписи → CDN 403 → файл не сохраняется."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.bots.outsee import (
    _MIN_IMAGE_BYTES,
    _all_full_png_url_candidates,
    _collect_download_url_candidates,
    _download_via_context_candidates,
    _is_outsee_thumb_url,
    _resolve_best_download_url,
)


SIGNED_THUMB = (
    "https://storage.yandexcloud.net/outseehistory/generated/3787/157627/"
    "outsee-157627-1780991092050_thumb.jpg"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc123"
)


def test_full_png_candidates_preserve_signed_query() -> None:
    cands = _all_full_png_url_candidates(SIGNED_THUMB)
    signed_full = [
        c
        for c in cands
        if (not _is_outsee_thumb_url(c)) and "X-Amz-Signature=abc123" in c
    ]
    assert signed_full, f"expected signed full PNG in {cands}"
    assert signed_full[0].endswith(".png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=abc123") or (
        ".png?" in signed_full[0] and "X-Amz-Signature=abc123" in signed_full[0]
    )


def test_resolve_prefers_signed_full_over_unsigned() -> None:
    resolved = _resolve_best_download_url(SIGNED_THUMB)
    assert _is_outsee_thumb_url(resolved) is False
    assert "X-Amz-Signature=abc123" in resolved
    assert "_thumb" not in resolved


def test_collect_puts_signed_full_first() -> None:
    cands = _collect_download_url_candidates(SIGNED_THUMB)
    assert cands
    assert _is_outsee_thumb_url(cands[0]) is False
    assert "X-Amz-Signature" in cands[0]


@pytest.mark.asyncio
async def test_download_via_context_candidates_uses_signed_full(
    tmp_path: Path,
) -> None:
    """Симуляция: unsigned full → 403; signed full → 200KB PNG сохраняется."""
    out = tmp_path / "frame.png"
    page = MagicMock()
    page.context = MagicMock()

    async def fake_download(page, url, out_path, **kwargs):
        if "X-Amz-Signature" not in url:
            raise RuntimeError(f"download {url} failed: HTTP 403")
        if "_thumb" in url:
            raise RuntimeError("should not download thumb")
        out_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (_MIN_IMAGE_BYTES + 1000))

    with (
        patch(
            "app.bots.outsee._find_full_png_in_dom",
            AsyncMock(return_value=None),
        ),
        patch(
            "app.bots.outsee._download_via_context",
            AsyncMock(side_effect=fake_download),
        ),
    ):
        used = await _download_via_context_candidates(
            page, SIGNED_THUMB, out
        )

    assert out.is_file()
    assert out.stat().st_size >= _MIN_IMAGE_BYTES
    assert "X-Amz-Signature" in used
    assert "_thumb" not in used
