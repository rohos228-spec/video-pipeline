"""После Generate с prompt_id — сначала queue Download, не CDN cascade."""

from __future__ import annotations

import inspect

from app.bots import outsee as outsee_mod


def test_generate_image_download_tries_queue_before_id_cascade() -> None:
    src = inspect.getsource(outsee_mod.OutseeBot._generate_image_on_page)
    # Порядок: queue_mode Download раньше card_click / download_saved.
    q = src.find("await _download_via_queue_result(")
    c = src.find("await _download_via_card_click(")
    d = src.find("await download_saved_image_by_prompt_id(")
    assert q >= 0, "queue download missing after Generate"
    assert c >= 0 and d >= 0
    assert q < c < d, (
        f"wrong order: queue@{q} card@{c} saved@{d} — "
        "montage prompt_id must not skip queue Download"
    )


def test_retry_image_download_tries_queue_first() -> None:
    src = inspect.getsource(outsee_mod.OutseeBot.retry_image_download)
    q = src.find("await _download_via_queue_result(")
    s = src.find("await download_saved_image_by_prompt_id(")
    assert q >= 0 and s >= 0
    assert q < s


def test_handoff_url_match_does_not_return_none_for_cdn_only() -> None:
    src = inspect.getsource(outsee_mod._find_card_by_clicking_images)
    assert "скачивание по CDN без клика" not in src
    assert 'return None' not in src.split("handoff URL совпал")[1].split("if not matched")[0]
    assert "_find_result_panel_card" in src.split("handoff URL совпал")[1][:800]
