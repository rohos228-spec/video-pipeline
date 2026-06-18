"""Тесты горизонтальной ленты кадров для anim_pr."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.services.image_strip import compose_horizontal_strip


def _solid(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    img = Image.new("RGB", size, color)
    img.save(path, format="PNG")


def test_compose_horizontal_strip_layout(tmp_path: Path) -> None:
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    p3 = tmp_path / "c.png"
    _solid(p1, (40, 80), (255, 0, 0))
    _solid(p2, (60, 120), (0, 255, 0))
    _solid(p3, (20, 40), (0, 0, 255))

    out = tmp_path / "strip.png"
    compose_horizontal_strip(
        [p1, p2, p3],
        out,
        gutter_px=4,
        max_height=80,
    )

    assert out.is_file()
    strip = Image.open(out)
    try:
        assert strip.size == (40 + 4 + 40 + 4 + 40, 80)
        assert strip.getpixel((20, 40)) == (255, 0, 0)
        assert strip.getpixel((42, 40)) == (255, 255, 255)
        assert strip.getpixel((64, 40)) == (0, 255, 0)
        assert strip.getpixel((86, 40)) == (255, 255, 255)
        assert strip.getpixel((108, 40)) == (0, 0, 255)
    finally:
        strip.close()


def test_build_batch_strip_path(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from app.services.animation_prompt_gpt import (
        FrameImageBatchItem,
        build_batch_strip_path,
    )

    paths = []
    for i, color in enumerate([(255, 0, 0), (0, 255, 0)], start=1):
        p = tmp_path / f"f{i}.png"
        _solid(p, (30, 60), color)
        paths.append(p)

    items = [
        FrameImageBatchItem(
            frame=SimpleNamespace(number=i),
            image_path=paths[i - 1],
            image_id=f"[ID: P1-F{i}-abc]",
            voiceover=f"text {i}",
        )
        for i in (1, 2)
    ]
    out = build_batch_strip_path(items, tmp_path / "out")
    assert out.name == "anim_pr_strip_001_002.png"
    assert out.is_file()
