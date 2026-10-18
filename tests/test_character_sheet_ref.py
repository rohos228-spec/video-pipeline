"""Кроп переднего роста с 16:9 turnaround-листа."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.services.character_sheet_ref import identity_ref_from_sheet


def test_identity_ref_crops_front_of_wide_sheet(tmp_path: Path) -> None:
    sheet = tmp_path / "c02.png"
    im = Image.new("RGB", (2048, 1152), (240, 240, 240))
    # Левая колонка «перед» — красная, остальное серое.
    for x in range(30, 280):
        for y in range(40, 1110):
            im.putpixel((x, y), (200, 20, 20))
    im.save(sheet)

    cache = tmp_path / "crops"
    cropped = identity_ref_from_sheet(sheet, cache)
    assert cropped != sheet
    assert cropped.name == "c02_front.png"
    out = Image.open(cropped)
    w, h = out.size
    assert w < 400
    assert h > 900
    # Кроп должен попасть в красную зону.
    px = out.getpixel((w // 2, h // 2))
    assert px[0] > 150 and px[1] < 60


def test_identity_ref_keeps_portrait_unchanged(tmp_path: Path) -> None:
    src = tmp_path / "c01.png"
    Image.new("RGB", (768, 1024), (10, 10, 10)).save(src)
    assert identity_ref_from_sheet(src, tmp_path / "crops") == src
