"""Outsee page URLs must carry the selected model slug (image + video)."""

from __future__ import annotations

from app.bots.outsee import _image_page_url, _video_page_url


def test_image_page_url_replaces_default_model() -> None:
    url = _image_page_url("gpt-image-2")
    assert "model=gpt-image-2" in url
    assert "nano-banana" not in url.split("model=")[-1]


def test_video_page_url_replaces_default_model() -> None:
    url = _video_page_url("veo-3-fast")
    assert "model=veo-3-fast" in url
    assert "veo-3-1-lite" not in url.split("model=")[-1]


def test_video_page_url_differs_per_generator() -> None:
    a = _video_page_url("kling-3-0")
    b = _video_page_url("seedance-2")
    assert a != b
    assert "model=kling-3-0" in a
    assert "model=seedance-2" in b
