"""Репро и контракт CDN URL: подпись thumb≠png (SigV4 path-bound).

Рабочее скачивание — реальный full URL из DOM/сети или кнопка «Скачать».
Копирование X-Amz-Signature с *_thumb.jpg на *.png → всегда 403.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlparse

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
    "image_1780991092050_0_thumb.jpg"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=thumbSIG"
)

REAL_SIGNED_FULL = (
    "https://outseehistory.storage.yandexcloud.net/generated/3787/157627/"
    "image_1780991092050_0.png"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=fullSIG"
)


def test_thumb_guess_does_not_reuse_thumb_signature() -> None:
    """Подпись thumb.jpg нельзя клеить на .png — path-bound SigV4."""
    cands = _all_full_png_url_candidates(SIGNED_THUMB)
    assert cands, "expected guessed full PNG paths"
    assert all("_thumb" not in c for c in cands)
    assert all("thumbSIG" not in c for c in cands), (
        f"thumb signature leaked onto PNG candidates: {cands}"
    )
    assert all(c.lower().endswith(".png") for c in cands)


def test_real_full_url_keeps_its_own_signature() -> None:
    cands = _all_full_png_url_candidates(REAL_SIGNED_FULL)
    assert any("fullSIG" in c for c in cands)


def test_resolve_prefers_real_signed_full_from_net_events() -> None:
    resolved = _resolve_best_download_url(
        SIGNED_THUMB, net_events=[(1.0, REAL_SIGNED_FULL)]
    )
    assert "fullSIG" in resolved
    assert _is_outsee_thumb_url(resolved) is False


def test_collect_puts_real_signed_full_first() -> None:
    cands = _collect_download_url_candidates(
        SIGNED_THUMB, extra_urls=[REAL_SIGNED_FULL]
    )
    assert cands
    assert "fullSIG" in cands[0]
    assert _is_outsee_thumb_url(cands[0]) is False


def test_guessed_png_path_differs_from_thumb_path() -> None:
    cands = _all_full_png_url_candidates(SIGNED_THUMB)
    thumb_name = Path(urlparse(SIGNED_THUMB).path).name
    for c in cands:
        assert Path(urlparse(c).path).name != thumb_name


@pytest.mark.asyncio
async def test_download_uses_real_dom_full_not_fake_thumb_sig(
    tmp_path: Path,
) -> None:
    """Без DOM full — CDN-only с thumb падает; с реальным full — ок."""
    out = tmp_path / "frame.png"
    page = MagicMock()
    page.context = MagicMock()

    async def fake_download(page, url, out_path, **kwargs):
        if "fullSIG" in url:
            out_path.write_bytes(
                b"\x89PNG\r\n\x1a\n" + b"x" * (_MIN_IMAGE_BYTES + 1000)
            )
            return
        if "thumbSIG" in url:
            raise RuntimeError("HTTP 403 path-signature mismatch")
        raise RuntimeError(f"download {url} failed: HTTP 403")

    with (
        patch(
            "app.bots.outsee._find_full_png_in_dom",
            AsyncMock(return_value=REAL_SIGNED_FULL),
        ),
        patch(
            "app.bots.outsee._download_via_context",
            AsyncMock(side_effect=fake_download),
        ),
    ):
        used = await _download_via_context_candidates(page, SIGNED_THUMB, out)

    assert out.is_file()
    assert out.stat().st_size >= _MIN_IMAGE_BYTES
    assert "fullSIG" in used
    assert "thumbSIG" not in used
