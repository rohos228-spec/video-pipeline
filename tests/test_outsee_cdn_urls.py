"""CDN URL guessing for outsee thumb → full PNG."""

from app.bots.outsee import (
    _all_full_png_url_candidates,
    _resolve_best_download_url,
)


def test_thumb_yields_both_cdn_hosts() -> None:
    thumb = (
        "https://storage.yandexcloud.net/outseehistory/generated/3787/135818/"
        "image_1780278102477_0_thumb.jpg?X-Amz-Algorithm=AWS4"
    )
    candidates = _all_full_png_url_candidates(thumb)
    assert any("outseehistory.storage.yandexcloud.net" in u for u in candidates)
    assert any("storage.yandexcloud.net/outseehistory" in u for u in candidates)
    assert all("_thumb" not in u for u in candidates)
    assert all("image_1780278102477_0.png" in u for u in candidates)
    assert all("_0_0.png" not in u for u in candidates)
    # Подпись thumb НЕ переносится на png (SigV4 path-bound).
    assert all("X-Amz-Algorithm" not in u for u in candidates)


def test_frame8_style_thumb_not_double_zero() -> None:
    """Регрессия: …_0_thumb.jpg не должен стать …_0_0.png."""
    thumb = (
        "https://storage.yandexcloud.net/outseehistory/generated/3787/135831/"
        "image_1780279147074_0_thumb.jpg"
    )
    candidates = _all_full_png_url_candidates(thumb)
    assert len(candidates) >= 2
    assert all("image_1780279147074_0.png" in u for u in candidates)
    assert not any("_0_0.png" in u for u in candidates)


def test_resolve_prefers_full_png_over_thumb() -> None:
    thumb = (
        "https://storage.yandexcloud.net/outseehistory/generated/1/2/"
        "image_100_0_thumb.jpg"
    )
    full = (
        "https://outseehistory.storage.yandexcloud.net/generated/1/2/"
        "image_100_0.png"
    )
    best = _resolve_best_download_url(thumb, extra_urls=[full])
    assert best == full
    assert "image_100_0.png" in best
    assert "_0_0.png" not in best
    assert "_thumb" not in best
