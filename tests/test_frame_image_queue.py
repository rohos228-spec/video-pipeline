"""Очередь generate_images: диск важнее статуса в БД."""

from __future__ import annotations

from pathlib import Path

from app.models import Frame, FrameStatus
from app.services.scan_frames import frame_needs_shot1_image, is_valid_scene_image


def _frame(n: int, status: FrameStatus, prompt: str = "p") -> Frame:
    fr = Frame(project_id=1, number=n, voiceover_text="v")
    fr.status = status
    fr.image_prompt = prompt
    return fr


def test_image_generated_without_file_still_needs_outsee(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    fr = _frame(3, FrameStatus.image_generated)
    assert frame_needs_shot1_image(fr, scenes) is True


def test_valid_png_on_disk_skips_generation(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    png = scenes / "frame_003_abcd1234.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 250_000)
    assert is_valid_scene_image(png)
    fr = _frame(3, FrameStatus.image_prompt_ready)
    assert frame_needs_shot1_image(fr, scenes) is False


def test_tiny_png_still_needs_generation(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    png = scenes / "frame_003_abcd1234.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 50_000)
    fr = _frame(3, FrameStatus.image_generated)
    assert frame_needs_shot1_image(fr, scenes) is True
