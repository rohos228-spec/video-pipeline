"""Валидация скачанного outsee-файла: thumb URL не должен убивать полный PNG."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.bots.outsee import (
    OutseeDownloadError,
    _MIN_IMAGE_BYTES,
    _validate_downloaded_image,
)


def _png_bytes(size: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"x" * max(0, size - 8)


def test_validate_accepts_full_png_even_if_url_is_thumb() -> None:
    """Browser-Download даёт полный PNG, wait часто оставляет *_thumb.jpg URL."""
    thumb_url = (
        "https://storage.yandexcloud.net/outseehistory/generated/3787/157627/"
        "outsee-157627-1780991092050_thumb.jpg?sig=1"
    )
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(_png_bytes(_MIN_IMAGE_BYTES + 50_000))
        path = Path(f.name)
    try:
        _validate_downloaded_image(path, gen_id="abc12345", img_url=thumb_url)
        assert path.is_file()
        assert path.stat().st_size >= _MIN_IMAGE_BYTES
    finally:
        path.unlink(missing_ok=True)


def test_validate_rejects_small_thumb_bytes() -> None:
    thumb_url = (
        "https://storage.yandexcloud.net/outseehistory/generated/1/2/"
        "outsee-2-1_thumb.jpg"
    )
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
        f.write(_png_bytes(12_000))
        path = Path(f.name)
    try:
        with pytest.raises(OutseeDownloadError) as ei:
            _validate_downloaded_image(path, gen_id="abc", img_url=thumb_url)
        assert "thumb" in str(ei.value).lower()
        assert not path.exists()
    finally:
        path.unlink(missing_ok=True)


def test_validate_accepts_full_url_full_png() -> None:
    full_url = (
        "https://storage.yandexcloud.net/outseehistory/generated/1/2/"
        "outsee-2-1.png"
    )
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(_png_bytes(_MIN_IMAGE_BYTES + 10_000))
        path = Path(f.name)
    try:
        _validate_downloaded_image(path, gen_id="abc12345", img_url=full_url)
    finally:
        path.unlink(missing_ok=True)
