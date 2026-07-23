"""Pricing + local generation storage."""

from __future__ import annotations

from pathlib import Path

from app.services.generation_storage import (
    build_generation_path,
    list_generation_files,
    write_sidecar,
)
from app.services.grsai_pricing import TOKEN_USD, quote_generation


def test_token_usd_rate():
    assert TOKEN_USD == 0.10


def test_quote_gpt_image_2():
    q = quote_generation(media="image", model="gpt-image-2", resolution="1K")
    assert q["tokens"] == 0.5
    assert q["usd"] == 0.05
    assert "ток" in q["label"]
    assert "$0.05" in q["label"]


def test_quote_resolution_multiplier():
    q1 = quote_generation(media="image", model="nano-banana-2", resolution="1K")
    q4 = quote_generation(media="image", model="nano-banana-2", resolution="4K")
    assert q4["tokens"] == round(q1["tokens"] * 2, 1) or q4["tokens"] > q1["tokens"]


def test_quote_sora_size_duration():
    small = quote_generation(
        media="video", model="sora-2", duration=10, size="small"
    )
    large15 = quote_generation(
        media="video", model="sora-2", duration=15, size="large"
    )
    assert large15["usd"] > small["usd"]


def test_build_generation_path_and_sidecar(tmp_path, monkeypatch):
    from app import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "data_dir", tmp_path)
    path = build_generation_path(media="image", model="gpt-image-2", ext=".png")
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    assert "generations" in path.parts
    assert "image" in path.parts
    assert "gpt-image-2" in path.parts
    side = write_sidecar(
        path,
        media="image",
        model="gpt-image-2",
        prompt="a cat",
        params={"aspect": "1:1"},
        quote=quote_generation(media="image", model="gpt-image-2"),
    )
    assert side.is_file()
    items = list_generation_files(kind="image", limit=10)
    assert any(i["path"] == str(path.resolve()) for i in items)
    assert any(i.get("prompt") == "a cat" for i in items)
