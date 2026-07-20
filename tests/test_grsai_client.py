"""Unit tests for Grsai client (no live API)."""

from __future__ import annotations

from app.bots.grsai import (
    GRSAI_WIRED_IMAGE_MODELS,
    build_generate_body,
    studio_id_to_grsai_slug,
)


def test_wired_models_include_gpt_and_banana():
    assert "gpt-image-2" in GRSAI_WIRED_IMAGE_MODELS
    assert "nano-banana-2" in GRSAI_WIRED_IMAGE_MODELS
    assert "nano-banana-pro" in GRSAI_WIRED_IMAGE_MODELS


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


def test_studio_id_to_grsai_slug():
    assert studio_id_to_grsai_slug("gpt_image_2") == "gpt-image-2"
    assert studio_id_to_grsai_slug("nano_banana_pro") == "nano-banana-pro"
    assert studio_id_to_grsai_slug("gpt-image-2") == "gpt-image-2"
