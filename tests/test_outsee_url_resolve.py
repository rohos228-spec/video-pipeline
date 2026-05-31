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
    assert resolved == full
    assert _is_outsee_thumb_url(resolved) is False


def test_collect_candidates_full_first() -> None:
    thumb = (
        "https://storage.yandexcloud.net/x/image_100_0_thumb.jpg?s=1"
    )
    full = "https://storage.yandexcloud.net/x/image_100_0.png?s=2"
    cands = _collect_download_url_candidates(
        thumb, net_events=[(0.0, thumb), (1.0, full)]
    )
    assert cands[0] == full
