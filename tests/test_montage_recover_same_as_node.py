"""Recover: поиск = strategy C ноды; скачивание = download_image_like_generate."""

from __future__ import annotations

import inspect

from app.bots import outsee as outsee_mod
from app.services import montage_outsee_recover as recover_mod


def test_recover_search_uses_strategy_c() -> None:
    src = inspect.getsource(recover_mod.recover_montage_images_from_outsee)
    assert "discover_prompt_ids_strategy_c" in src
    assert "run_five_mechanics_search" not in src
    assert "scan_gallery_hits_for_project" not in src
    assert "scan_gallery_hits_by_clicking" not in src


def test_recover_download_uses_generate_path() -> None:
    src = inspect.getsource(recover_mod._download_hit)
    assert "download_image_like_generate" in src
    assert "download_with_all_mechanics" not in src
    assert "download_d3" not in src


def test_strategy_c_discover_mirrors_card_click_scan() -> None:
    disc = inspect.getsource(outsee_mod.discover_prompt_ids_strategy_c)
    card = inspect.getsource(outsee_mod._find_card_by_clicking_images)
    assert "_recent_big_gallery_img_srcs" in disc
    assert "_recent_big_gallery_img_srcs" in card
    assert "_physical_mouse_click" in disc
    assert "prefer_cdp=True" in disc


def test_generate_and_recover_share_download() -> None:
    gen = inspect.getsource(outsee_mod.OutseeBot._generate_image_on_page)
    assert "download_image_like_generate" in gen
    shared = inspect.getsource(outsee_mod.download_image_like_generate)
    q = shared.find("await _download_via_queue_result(")
    c = shared.find("await _download_via_card_click(")
    d = shared.find("await download_saved_image_by_prompt_id(")
    assert 0 <= q < c < d


def test_recover_before_regen_does_not_force_wipe() -> None:
    src = inspect.getsource(recover_mod.recover_before_regen_ops)
    assert "force_replace=False" in src
