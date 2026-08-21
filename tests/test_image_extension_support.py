"""Тесты распознавания расширений .png, .jpg, .jpeg, .webp при сканировании кадров."""

import pytest
from pathlib import Path
from app.services.scan_frames import newest_frame_image_path
from app.services.plan_shot2 import find_shot1_image, find_shot2_image, disk_has_shot2_image


def test_newest_frame_image_path_finds_webp_and_jpeg(tmp_path: Path):
    """Проверяет, что newest_frame_image_path находит файлы .webp и .jpg."""
    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir()
    
    # Кадр 1 — .webp
    f1_webp = scenes_dir / "frame_001_abc.webp"
    f1_webp.write_bytes(b"webp_content")
    assert newest_frame_image_path(scenes_dir, 1) == f1_webp
    assert find_shot1_image(scenes_dir, 1) == f1_webp

    # Кадр 2 — .jpg
    f2_jpg = scenes_dir / "frame_002_def.jpg"
    f2_jpg.write_bytes(b"jpg_content")
    assert newest_frame_image_path(scenes_dir, 2) == f2_jpg
    assert find_shot1_image(scenes_dir, 2) == f2_jpg

    # Кадр 3 shot2 — .jpeg
    f3_s2_jpeg = scenes_dir / "frame_003_s2_ghi.jpeg"
    f3_s2_jpeg.write_bytes(b"s2_jpeg_content")
    assert disk_has_shot2_image(scenes_dir, 3) is True
    assert find_shot2_image(scenes_dir, 3) == f3_s2_jpeg