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


def test_compose_horizontal_strip_burns_panel_labels(tmp_path: Path) -> None:
    p1 = tmp_path / "a.png"
    p2 = tmp_path / "b.png"
    _solid(p1, (80, 80), (255, 0, 0))
    _solid(p2, (80, 80), (0, 255, 0))
    out = compose_horizontal_strip(
        [p1, p2],
        tmp_path / "labeled.png",
        gutter_px=4,
        max_height=80,
        panel_labels=["F001", "F002"],
    )
    strip = Image.open(out)
    try:
        # Нижняя полоса первой панели — чёрная (метка).
        assert strip.getpixel((10, 75)) == (0, 0, 0)
        # Центр панели выше полосы — красный.
        assert strip.getpixel((40, 30)) == (255, 0, 0)
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
    assert out.name.startswith("anim_pr_strip_001_002")
    assert out.suffix in {".png", ".jpg"}
    assert out.is_file()


def test_compose_strip_respects_max_bytes(tmp_path: Path) -> None:
    """Широкая лента 16:9 должна ужаться под лимит vision."""
    paths = []
    for i in range(5):
        p = tmp_path / f"f{i}.png"
        # Несжимаемый шум → PNG лента легко >1MB без max_bytes.
        import random

        img = Image.new("RGB", (1280, 720))
        pix = img.load()
        rng = random.Random(i)
        for y in range(0, 720, 4):
            for x in range(0, 1280, 4):
                pix[x, y] = (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        img.save(p, format="PNG")
        paths.append(p)

    out = compose_horizontal_strip(
        paths,
        tmp_path / "strip.png",
        gutter_px=6,
        max_height=768,
        max_bytes=3_500_000,
    )
    assert out.is_file()
    assert out.stat().st_size <= 3_500_000
