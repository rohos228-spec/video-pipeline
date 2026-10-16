
from __future__ import annotations

from pathlib import Path

from loguru import logger

_SHEET_MIN_ASPECT = 1.3
_FRONT_LEFT = 0.012
_FRONT_RIGHT = 0.148
_FRONT_TOP = 0.025
_FRONT_BOTTOM = 0.975


def identity_ref_from_sheet(sheet_path: Path, cache_dir: Path) -> Path:
    src = Path(sheet_path)
    if not src.is_file():
        return src
    dest = Path(cache_dir) / f"{src.stem}_front.png"
    try:
        if dest.is_file() and dest.stat().st_mtime >= src.stat().st_mtime:
            if dest.stat().st_size > 10_000:
                return dest
        cropped = _crop_front_body(src, dest)
    except Exception as exc:
        logger.warning("character_sheet_ref: crop {} failed: {}", src.name, exc)
        return src
    return cropped if cropped is not None else src


def _crop_front_body(src: Path, dest: Path) -> Path | None:
    from PIL import Image

    with Image.open(src) as im:
        w, h = im.size
        if w < 800 or h < 400 or (w / float(h)) < _SHEET_MIN_ASPECT:
            return None
        box = (
            int(w * _FRONT_LEFT),
            int(h * _FRONT_TOP),
            int(w * _FRONT_RIGHT),
            int(h * _FRONT_BOTTOM),
        )
        if box[2] - box[0] < 80 or box[3] - box[1] < 160:
            return None
        crop = im.crop(box)
        dest.parent.mkdir(parents=True, exist_ok=True)
        crop.save(dest, "PNG")
    return dest
