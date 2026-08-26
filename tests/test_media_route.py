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


def test_image_forced_outsee_even_if_grsai(monkeypatch) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER", "grsai")
    from app.settings import Settings
    import app.services.media_route as mr
    import app.settings as settings_mod

    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(mr, "settings", s)
    assert image_provider_for("gpt-image-2-vip") == "outsee"
    assert image_provider_for("nano-banana-2") == "outsee"
    assert image_provider_for("nano_banana_2_lite") == "outsee"
    assert image_provider_for("nano-banana-pro") == "grsai"
    assert image_provider_for("nano-banana") == "grsai"


def test_nano_banana_pro_never_outsee_even_if_image_provider_outsee(monkeypatch) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER", "outsee")
    from app.settings import Settings
    import app.services.media_route as mr
    import app.settings as settings_mod

    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(mr, "settings", s)
    for slug in (
        "nano-banana-pro",
        "nano_banana_pro",
        "nano-banana-pro-vt",
        "nano-banana-pro-vip",
    ):
        assert mr.image_provider_for(slug) != "outsee"
        assert mr.is_nano_banana_pro(slug) is True
    assert mr.is_nano_banana_pro("nano-banana-2") is False
    assert mr.image_provider_for("nano-banana-2") == "outsee"


def test_video_veo_outsee_kling_kie(monkeypatch) -> None:
    monkeypatch.setenv("VIDEO_PROVIDER", "grsai")
    from app.settings import Settings
    import app.services.media_route as mr
    import app.settings as settings_mod

    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(mr, "settings", s)
    assert video_provider_for("veo-3-1-lite") == "outsee"
    assert video_provider_for("veo_3_1_fast") == "outsee"
    assert video_provider_for("kling-2-6") == "kie"
    assert video_provider_for("kling_2_6") == "kie"
    assert video_provider_for("sora-2") == "grsai"
