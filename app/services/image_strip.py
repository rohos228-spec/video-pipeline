"""Горизонтальная лента из нескольких кадров с белыми вертикальными разделителями."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

DEFAULT_GUTTER_PX = 6
DEFAULT_MAX_HEIGHT_PX = 768
_GUTTER_RGB = (255, 255, 255)


def _panel_font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _burn_panel_label(img: Image.Image, label: str) -> Image.Image:
    """Чёрная полоса снизу с белым ID кадра — чтобы vision не путал панели."""
    text = (label or "").strip()
    if not text:
        return img
    out = img.copy()
    draw = ImageDraw.Draw(out)
    bar_h = max(20, min(48, out.height // 10))
    draw.rectangle((0, out.height - bar_h, out.width, out.height), fill=(0, 0, 0))
    font = _panel_font(max(14, bar_h - 8))
    # чуть отступ от края полосы
    draw.text((6, out.height - bar_h + 3), text, fill=(255, 255, 255), font=font)
    return out


def compose_horizontal_strip(
    image_paths: list[Path],
    out_path: Path,
    *,
    gutter_px: int = DEFAULT_GUTTER_PX,
    max_height: int | None = DEFAULT_MAX_HEIGHT_PX,
    gutter_color: tuple[int, int, int] = _GUTTER_RGB,
    max_bytes: int | None = 3_500_000,
    panel_labels: list[str] | None = None,
) -> Path:
    """Склеивает изображения слева направо; между ними — тонкие вертикальные полоски.

    Если max_bytes задан и PNG не влезает — сохраняет JPEG (`.jpg`) с понижением
    высоты/качества (нужно для GPT vision ≤4MB). Возвращает фактический путь.
    """
    if not image_paths:
        raise ValueError("compose_horizontal_strip: image_paths пустой")
    labels = list(panel_labels or [])
    if labels and len(labels) != len(image_paths):
        raise ValueError(
            f"compose_horizontal_strip: panel_labels={len(labels)} "
            f"≠ images={len(image_paths)}"
        )

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
                for idx, img in enumerate(opened):
                    if img.height == target_h:
                        panel = img.copy()
                    else:
                        new_w = max(1, int(img.width * target_h / img.height))
                        panel = img.resize((new_w, target_h), Image.Resampling.LANCZOS)
                    if labels:
                        labeled = _burn_panel_label(panel, labels[idx])
                        panel.close()
                        panel = labeled
                    scaled.append(panel)

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


def compose_grid_strip(
    image_paths: list[Path],
    out_path: Path,
    *,
    cols: int = 3,
    rows: int = 2,
    gutter_px: int = DEFAULT_GUTTER_PX,
    max_cell_height: int | None = 384,
    gutter_color: tuple[int, int, int] = _GUTTER_RGB,
    max_bytes: int | None = 3_500_000,
) -> Path:
    """Сетка кадров слева→направо, сверху→вниз (как 3×2 для video_sheet).

    Порядок: row0 = paths[0:cols], row1 = paths[cols:2*cols], …
    Между ячейками — белые gutters. Возвращает фактический путь (.png/.jpg).
    """
    if not image_paths:
        raise ValueError("compose_grid_strip: image_paths пустой")
    if cols < 1 or rows < 1:
        raise ValueError("compose_grid_strip: cols/rows должны быть ≥ 1")
    expected = cols * rows
    if len(image_paths) != expected:
        raise ValueError(
            f"compose_grid_strip: нужно ровно {expected} кадров, дано {len(image_paths)}"
        )

    opened: list[Image.Image] = []
    try:
        for fp in image_paths:
            if not fp.is_file():
                raise FileNotFoundError(f"compose_grid_strip: нет файла {fp}")
            opened.append(Image.open(fp).convert("RGB"))

        natural_h = max(img.height for img in opened)
        base_h = natural_h
        if max_cell_height is not None and max_cell_height > 0:
            base_h = min(base_h, max_cell_height)

        heights = [base_h]
        if max_bytes is not None and max_bytes > 0:
            for h in (288, 216, 160):
                if h < base_h and h not in heights:
                    heights.append(h)

        gutter = max(1, gutter_px)

        for target_h in heights:
            cells: list[Image.Image] = []
            canvas: Image.Image | None = None
            try:
                raw_scaled: list[Image.Image] = []
                for img in opened:
                    if img.height == target_h:
                        raw_scaled.append(img.copy())
                    else:
                        new_w = max(1, int(img.width * target_h / img.height))
                        raw_scaled.append(
                            img.resize((new_w, target_h), Image.Resampling.LANCZOS)
                        )
                cell_w = max(img.width for img in raw_scaled)
                for img in raw_scaled:
                    if img.width == cell_w:
                        cells.append(img)
                        continue
                    cell = Image.new("RGB", (cell_w, target_h), gutter_color)
                    cell.paste(img, ((cell_w - img.width) // 2, 0))
                    img.close()
                    cells.append(cell)

                total_w = cell_w * cols + gutter * (cols - 1)
                total_h = target_h * rows + gutter * (rows - 1)
                canvas = Image.new("RGB", (total_w, total_h), gutter_color)
                for idx, img in enumerate(cells):
                    r, c = divmod(idx, cols)
                    canvas.paste(img, (c * (cell_w + gutter), r * (target_h + gutter)))

                out_path.parent.mkdir(parents=True, exist_ok=True)
                png_path = out_path.with_suffix(".png")
                canvas.save(png_path, format="PNG", optimize=True)
                size = png_path.stat().st_size
                if max_bytes is None or size <= max_bytes:
                    logger.info(
                        "image_strip grid: {} → {} ({}×{} px, {}×{}, {} bytes)",
                        len(image_paths),
                        png_path.name,
                        total_w,
                        total_h,
                        cols,
                        rows,
                        size,
                    )
                    return png_path

                png_path.unlink(missing_ok=True)
                jpg_path = out_path.with_suffix(".jpg")
                for quality in (85, 70, 55, 40):
                    canvas.save(jpg_path, format="JPEG", quality=quality, optimize=True)
                    size = jpg_path.stat().st_size
                    if max_bytes is None or size <= max_bytes:
                        logger.info(
                            "image_strip grid: {} → {} ({}×{} px, jpeg q={}, {} bytes)",
                            len(image_paths),
                            jpg_path.name,
                            total_w,
                            total_h,
                            quality,
                            size,
                        )
                        return jpg_path
                jpg_path.unlink(missing_ok=True)
            finally:
                if canvas is not None:
                    canvas.close()
                for cell in cells:
                    cell.close()

        raise RuntimeError(
            f"compose_grid_strip: не удалось ужать сетку до {max_bytes} байт"
        )
    finally:
        for img in opened:
            img.close()
