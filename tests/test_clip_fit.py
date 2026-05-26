"""Тесты подгонки длительности клипов и разбора имён файлов."""

from pathlib import Path

from app.services.assembly import parse_frame_number_from_path
from app.services.clip_fit import plan_clip_fit


def test_plan_clip_shorter_source():
    p = plan_clip_fit(2.0, 4.0)
    assert p.mode == "use_source"
    assert p.output_duration == 2.0


def test_plan_clip_stretch_within_15pct():
    # 8s → 7s: ratio 8/7 ≈ 1.14 ≤ 1.15
    p = plan_clip_fit(8.0, 7.0, max_stretch_ratio=0.15)
    assert p.mode == "stretch"
    assert p.output_duration == 7.0


def test_plan_clip_trim_when_too_long():
    p = plan_clip_fit(10.0, 5.0, max_stretch_ratio=0.15)
    assert p.mode == "trim"
    assert p.output_duration == 5.0


def test_parse_clip_filename():
    assert parse_frame_number_from_path(Path("clip_003_ab12cd.mp4")) == 3
    assert parse_frame_number_from_path(Path("/videos/clip_012_x.mp4")) == 12
