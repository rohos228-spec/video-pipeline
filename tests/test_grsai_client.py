"""Unit tests for Grsai client (no live API)."""

from __future__ import annotations

from app.bots.grsai import (
    GRSAI_WIRED_AUDIO_MODELS,
    GRSAI_WIRED_IMAGE_MODELS,
    GRSAI_WIRED_VIDEO_MODELS,
    build_generate_body,
    build_video_body,
    studio_id_to_grsai_slug,
    studio_id_to_grsai_video_slug,
)


def test_wired_models_include_gpt_and_banana():
    assert "gpt-image-2" in GRSAI_WIRED_IMAGE_MODELS
    assert "nano-banana-2" in GRSAI_WIRED_IMAGE_MODELS
    assert "nano-banana-pro" in GRSAI_WIRED_IMAGE_MODELS


def test_wired_video_models():
    assert "sora-2" in GRSAI_WIRED_VIDEO_MODELS
    assert "veo3.1-fast" in GRSAI_WIRED_VIDEO_MODELS
    assert "veo3.1-pro" in GRSAI_WIRED_VIDEO_MODELS


def test_wired_audio_empty_on_grsai():
    """Grsai getModelList currently has no Suno/TTS — keep empty until they appear."""
    assert GRSAI_WIRED_AUDIO_MODELS == ()


def test_build_generate_body_gpt_image():
    body = build_generate_body(
        model="gpt-image-2",
        prompt="a cat",
        aspect_ratio="9:16",
        resolution="1K",
    )
    assert body["model"] == "gpt-image-2"
    assert body["prompt"] == "a cat"
    assert body["replyType"] == "json"
    assert body["aspectRatio"] == "9:16"
    assert "imageSize" not in body


def test_build_generate_body_gpt_image_vip_pixels():
    body = build_generate_body(
        model="gpt-image-2-vip",
        prompt="poster",
        aspect_ratio="16:9",
        resolution="2K",
    )
    assert body["aspectRatio"] == "2048x1152"
    assert "imageSize" not in body


def test_build_generate_body_nano_banana():
    body = build_generate_body(
        model="nano-banana-2",
        prompt="portrait",
        aspect_ratio="9_16",
        resolution="2k",
    )
    assert body["aspectRatio"] == "9:16"
    assert body["imageSize"] == "2K"


def test_build_video_body_sora():
    path, body = build_video_body(
        model="sora-2",
        prompt="a cat",
        aspect_ratio="9:16",
        duration=10,
        size="small",
    )
    assert path == "/v1/video/sora-video"
    assert body["model"] == "sora-2"
    assert body["duration"] == 10
    assert body["webHook"] == "-1"


def test_build_video_body_veo():
    path, body = build_video_body(
        model="veo3.1-fast",
        prompt="a cat walking",
        aspect_ratio="16:9",
    )
    assert path == "/v1/video/veo"
    assert body["model"] == "veo3.1-fast"
    assert body["aspectRatio"] == "16:9"


def test_studio_id_to_grsai_slug():
    assert studio_id_to_grsai_slug("gpt_image_2") == "gpt-image-2"
    assert studio_id_to_grsai_slug("nano_banana_pro") == "nano-banana-pro"
    assert studio_id_to_grsai_slug("gpt-image-2") == "gpt-image-2"


def test_studio_id_to_grsai_video_slug():
    assert studio_id_to_grsai_video_slug("veo_3_1_lite") == "veo3.1-fast"
    assert studio_id_to_grsai_video_slug("sora-2") == "sora-2"
