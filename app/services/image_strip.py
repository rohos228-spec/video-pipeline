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
    max_bytes: int | None = 3_500_000,
) -> Path:
    """Склеивает изображения слева направо; между ними — тонкие вертикальные полоски.

    Если max_bytes задан и PNG не влезает — сохраняет JPEG (`.jpg`) с понижением
    высоты/качества (нужно для GPT vision ≤4MB). Возвращает фактический путь.
    """
    if not image_paths:
        raise ValueError("compose_horizontal_strip: image_paths пустой")

    opened: list[Image.Image] = []
    try:
        for fp in image_paths:
            if not fp.is_file():
                raise FileNotFoundError(f"compose_horizontal_strip: нет файла {fp}")
            opened.append(Image.open(fp).convert("RGB"))

        natural_h = max(img.height for img in opened)
        base_h = natural_h
        if max_height is not None and max_height > 0:
            base_h = min(base_h, max_height)

        heights = [base_h]
        if max_bytes is not None and max_bytes > 0:
            for h in (512, 384, 288, 216):
                if h < base_h and h not in heights:
                    heights.append(h)

        for target_h in heights:
            scaled: list[Image.Image] = []
            try:
                for img in opened:
                    if img.height == target_h:
                        scaled.append(img.copy())
                    else:
                        new_w = max(1, int(img.width * target_h / img.height))
                        scaled.append(
                            img.resize((new_w, target_h), Image.Resampling.LANCZOS)
                        )

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
                png_path = out_path.with_suffix(".png")
                canvas.save(png_path, format="PNG", optimize=True)
                size = png_path.stat().st_size
                if max_bytes is None or size <= max_bytes:
                    logger.info(
                        "image_strip: {} → {} ({}×{} px, gutter={}, {} bytes)",
                        len(image_paths),
                        png_path.name,
                        total_w,
                        target_h,
                        gutter,
                        size,
                    )
                    canvas.close()
                    return png_path

                png_path.unlink(missing_ok=True)
                jpg_path = out_path.with_suffix(".jpg")
                for quality in (85, 70, 55, 40):
                    canvas.save(jpg_path, format="JPEG", quality=quality, optimize=True)
                    size = jpg_path.stat().st_size
                    if max_bytes is None or size <= max_bytes:
                        logger.info(
                            "image_strip: {} → {} ({}×{} px, gutter={}, jpeg q={}, {} bytes)",
                            len(image_paths),
                            jpg_path.name,
                            total_w,
                            target_h,
                            gutter,
                            quality,
                            size,
                        )
                        canvas.close()
                        return jpg_path
                jpg_path.unlink(missing_ok=True)
                canvas.close()
            finally:
                for s in scaled:
                    s.close()

        raise RuntimeError(
            f"compose_horizontal_strip: не удалось ужать ленту до {max_bytes} байт"
        )
    finally:
        for img in opened:
            img.close()
