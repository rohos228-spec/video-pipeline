"""Тесты сетки 3×2 и токенов video_sheet для vision-check."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.services.check_analysis import (
    extract_ok_vision_tokens,
    normalize_frame_regen_token,
)
from app.services.image_strip import compose_grid_strip
from app.services.video_sheet import parse_clip_number


def _solid(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    Image.new("RGB", size, color).save(path, format="PNG")


def test_compose_grid_strip_3x2(tmp_path: Path) -> None:
    paths = []
    colors = [
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 255, 0),
        (255, 0, 255),
        (0, 255, 255),
    ]
    for i, color in enumerate(colors, start=1):
        p = tmp_path / f"c{i}.png"
        _solid(p, (30, 60), color)
        paths.append(p)

    out = compose_grid_strip(
        paths,
        tmp_path / "grid.png",
        cols=3,
        rows=2,
        gutter_px=4,
        max_cell_height=60,
        max_bytes=None,
    )
    assert out.is_file()
    img = Image.open(out)
    try:
        # 3 cells × 30 + 2 gutters × 4 = 98; 2 rows × 60 + gutter 4 = 124
        assert img.size == (30 * 3 + 4 * 2, 60 * 2 + 4)
        assert img.getpixel((15, 30)) == (255, 0, 0)  # cell 1
        assert img.getpixel((15, 90)) == (255, 255, 0)  # cell 4 (row2)
    finally:
        img.close()


def test_parse_clip_and_video_sheet_tokens() -> None:
    assert parse_clip_number(Path("clip_009_abc.mp4")) == (9, 1)
    assert parse_clip_number(Path("clip_009_s2_abc.mp4")) == (9, 2)
    assert parse_clip_number(Path("video_sheet_012_clip.png")) == (12, 1)

    tok = normalize_frame_regen_token("video_sheet_009_x.png")
    assert tok == {"number": 9, "shot": 1}
    tok2 = normalize_frame_regen_token("video_sheet_009_s2_x.jpg")
    assert tok2 == {"number": 9, "shot": 2}


def test_extract_ok_video_sheet_tokens() -> None:
    report = """
## findings
- [ok] video_sheet_003_clip.png: смысл и камера ок
- [critical] video_sheet_009_x.png cell:2: грубый warp
"""
    toks = extract_ok_vision_tokens(report)
    assert "f3" in toks


def test_extract_critical_video_sheet_frames() -> None:
    from app.services.check_analysis import extract_critical_frame_regen_targets

    report = """
# ОТЧЁТ ПРОВЕРКИ
verdict: fail
## scores
overall: 0.40
## issues
- [critical] video_sheet_009_x.png: грубый warp лица
- [warning] video_sheet_004_y.png: лёгкий flicker
"""
    tgts = extract_critical_frame_regen_targets(report)
    assert {"number": 9, "shot": 1} in tgts
    assert all(t["number"] != 4 for t in tgts)
