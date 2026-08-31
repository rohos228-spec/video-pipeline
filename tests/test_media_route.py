"""Per-model Outsee / kie routing."""

from __future__ import annotations

from app.services.media_route import (
    canonical_media_id,
    image_provider_for,
    video_provider_for,
)


def test_aliases_slow_gpt_image_to_vip() -> None:
    assert canonical_media_id("gpt-image-2") == "gpt-image-2-vip"
    assert canonical_media_id("gpt_image_2") == "gpt-image-2-vip"
    assert canonical_media_id("gpt-image-2-vip") == "gpt-image-2-vip"


def test_image_forced_outsee_even_if_grsai() -> None:
    assert image_provider_for("gpt-image-2-vip") == "outsee"
    assert image_provider_for("nano-banana-2") == "outsee"
    assert image_provider_for("nano_banana_2_lite") == "outsee"


def test_video_veo_outsee_kling_kie() -> None:
    assert video_provider_for("veo-3-1-lite") == "outsee"
    assert video_provider_for("veo_3_1_fast") == "outsee"
    assert video_provider_for("kling-2-6") == "kie"
    assert video_provider_for("kling_2_6") == "kie"
