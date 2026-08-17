"""Montage regen: CDP history-recover удалён (18ce622b), остаётся контракт download helper."""

from __future__ import annotations


def test_download_saved_image_by_prompt_id_skips_url_verify() -> None:
    """Контракт: helper не передаёт img_url в card-click."""
    import inspect

    from app.bots.outsee import download_saved_image_by_prompt_id

    src = inspect.getsource(download_saved_image_by_prompt_id)
    assert "img_url" not in src.split("_download_via_card_click")[1].split(")")[0]
    assert "_wait_gallery_thumbs" in src
