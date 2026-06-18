"""Горизонтальная лента из нескольких кадров с белыми вертикальными разделителями."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PIL import Image

DEFAULT_GUTTER_PX = 6
DEFAULT_MAX_HEIGHT_PX = 768
_GUTTER_RGB = (255, 255, 255)


def compose_horizontal_strip(
    image_paths: list[Path],
    out_path: Path,
    *,
    gutter_px: int = DEFAULT_GUTTER_PX,
    max_height: int | None = DEFAULT_MAX_HEIGHT_PX,
    gutter_color: tuple[int, int, int] = _GUTTER_RGB,
) -> Path:
    """Склеивает изображения слева направо; между ними — тонкие вертикальные полоски."""
    if not image_paths:
        raise ValueError("compose_horizontal_strip: image_paths пустой")

    opened: list[Image.Image] = []
    try:
        for fp in image_paths:
            if not fp.is_file():
                raise FileNotFoundError(f"compose_horizontal_strip: нет файла {fp}")
            opened.append(Image.open(fp).convert("RGB"))

        natural_h = max(img.height for img in opened)
        target_h = natural_h
        if max_height is not None and max_height > 0:
            target_h = min(target_h, max_height)

        scaled: list[Image.Image] = []
        for img in opened:
            if img.height == target_h:
                scaled.append(img)
                continue
            new_w = max(1, int(img.width * target_h / img.height))
            scaled.append(img.resize((new_w, target_h), Image.Resampling.LANCZOS))

        gutter = max(1, gutter_px)
        total_w = sum(img.width for img in scaled) + gutter * (len(scaled) - 1)
        canvas = Image.new("RGB", (total_w, target_h), gutter_color)

        x = 0
        for i, img in enumerate(scaled):
            canvas.paste(img, (x, 0))
            x += img.width
            if i < len(scaled) - 1:
                x += gutter

        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_path, format="PNG", optimize=True)
        logger.info(
            "image_strip: {} → {} ({}×{} px, gutter={})",
            len(image_paths),
            out_path.name,
            total_w,
            target_h,
            gutter,
        )
        return out_path
    finally:
        for img in opened:
            img.close()
