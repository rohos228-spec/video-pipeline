"""Тесты выбора full PNG вместо thumb для outsee download."""

from __future__ import annotations

from app.bots.outsee import (
    _collect_download_url_candidates,
    _is_outsee_thumb_url,
    _outsee_image_stable_key,
    _resolve_best_download_url,
)


def test_stable_key_from_thumb_and_full() -> None:
    thumb = (
        "https://storage.yandexcloud.net/outseehistory/generated/3787/133392/"
        "image_1780215069357_0_thumb.jpg?X-Amz-Signature=abc"
    )
    full = (
        "https://storage.yandexcloud.net/outseehistory/generated/3787/133392/"
        "image_1780215069357_0.png?X-Amz-Signature=def"
    )
    assert _outsee_image_stable_key(thumb) == "image_1780215069357_0"
    assert _outsee_image_stable_key(full) == "image_1780215069357_0"


def test_resolve_prefers_full_png_over_thumb() -> None:
    thumb = (
        "https://storage.yandexcloud.net/outseehistory/generated/3787/133392/"
        "image_1780215069357_0_thumb.jpg?sig=1"
    )
    full = (
        "https://storage.yandexcloud.net/outseehistory/generated/3787/133392/"
        "image_1780215069357_0.png?sig=2"
    )
    net = [(1.0, thumb), (2.0, full)]
    resolved = _resolve_best_download_url(thumb, net_events=net)
    assert _is_outsee_thumb_url(resolved) is False
    assert "image_1780215069357_0.png" in resolved
    assert resolved != thumb


def test_collect_candidates_full_first() -> None:
    thumb = (
        "https://storage.yandexcloud.net/x/image_100_0_thumb.jpg?s=1"
    )
    full = "https://storage.yandexcloud.net/x/image_100_0.png?s=2"
    cands = _collect_download_url_candidates(
        thumb, net_events=[(0.0, thumb), (1.0, full)]
    )
    assert cands
    assert _is_outsee_thumb_url(cands[0]) is False
    assert "image_100_0.png" in cands[0]


def test_validate_rejects_thumb_download() -> None:
    """Маленький файл + thumb URL — отказ. Полный PNG при thumb URL — ок (отдельный тест)."""
    from pathlib import Path
    import tempfile

    from app.bots.outsee import OutseeDownloadError, _validate_downloaded_image

    thumb_url = (
        "https://storage.yandexcloud.net/outseehistory/generated/3787/157627/"
        "outsee-157627-1780991092050_thumb.jpg?sig=1"
    )
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"x" * 12_000)
        path = Path(f.name)
    try:
        try:
            _validate_downloaded_image(path, gen_id="abc", img_url=thumb_url)
        except OutseeDownloadError as e:
            assert "thumb" in str(e).lower()
        else:
            raise AssertionError("expected OutseeDownloadError for small thumb")
    finally:
        path.unlink(missing_ok=True)
