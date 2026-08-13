"""Фейковые медиа-провайдеры: пишут реальные tiny-файлы в out_path.

Подменяют `outsee_retry.generate_image_with_retries` /
`generate_video_with_retries` на границе шагов img/video (и montage regen).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.bots.outsee import GenerationResult

_TINY_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


def write_tiny_png(path: Path, *, seed: int = 0) -> Path:
    """Валидный scene PNG: ≥200_000 байт (порог is_valid_scene_image)."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    color = (
        (60 + seed * 40) % 220,
        (120 + seed * 30) % 220,
        (180 + seed * 20) % 220,
    )
    # Без сжатия, чтобы размер гарантированно перевалил за 200 KB
    # (scan_frames._MIN_SCENE_IMAGE_BYTES), иначе кадр «без PNG» навсегда.
    Image.new("RGB", (320, 568), color).save(path, compress_level=0)
    return path


def write_tiny_mp4(path: Path, *, seed: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_TINY_MP4 + bytes([seed % 256]))
    return path


async def fake_generate_image_with_retries(
    outsee: Any,
    gpt: Any,
    *,
    prompt: str,
    out_path: Path,
    **kwargs: Any,
) -> GenerationResult:
    seed = len(prompt or "") % 7
    write_tiny_png(Path(out_path), seed=seed)
    return GenerationResult(file_path=Path(out_path), raw_url="fake://img")


async def fake_generate_video_with_retries(
    outsee: Any,
    gpt: Any,
    *,
    prompt: str,
    out_path: Path,
    **kwargs: Any,
) -> GenerationResult:
    seed = len(prompt or "") % 7
    write_tiny_mp4(Path(out_path), seed=seed)
    return GenerationResult(file_path=Path(out_path), raw_url="fake://video")
