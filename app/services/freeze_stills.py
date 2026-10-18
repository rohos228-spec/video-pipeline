"""Раскладка СТАРТ/КОНЕЦ → два still: shot1 начало, shot2 конец."""

from __future__ import annotations

import re
from contextlib import suppress
from pathlib import Path
from typing import Any

from app.services.plan_shot2 import SHOT2_PROMPT_ATTR, SHOT2_STATUS_ATTR, find_shot2_image

_START_RE = re.compile(
    r"СТАРТ\s*\([^)]*\)\s*:\s*(.+?)(?=\s*(?:План\b|Зона\b|Камера\b|Ракурс\b|"
    r"Предметы на старте:|КОНЕЦ\b|$))",
    re.IGNORECASE | re.DOTALL,
)
_END_RE = re.compile(
    r"КОНЕЦ\s*\([^)]*\)\s*:\s*(.+?)(?=\s*(?:Предметы в конце:|В кадре меняется:|"
    r"В референсе|Референса нет|Смена:|$))",
    re.IGNORECASE | re.DOTALL,
)
# Case-sensitive: scene_plan writes the markers upper-case; «старт»/«конец» in an action is not freeze.
_START_MARK_RE = re.compile(r"СТАРТ\s*\([^)]*\)\s*:")
_END_MARK_RE = re.compile(r"КОНЕЦ\s*\([^)]*\)\s*:")


def layout_text(frame: Any) -> str:
    """Текст раскладки кадра: attrs, иначе первый шоты[] с раскладкой."""
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict):
        if isinstance(frame, dict):
            attrs = frame.get("attrs") if isinstance(frame.get("attrs"), dict) else frame
        else:
            attrs = {}
    if not isinstance(attrs, dict):
        return ""
    raw = str(attrs.get("раскладка") or "").strip()
    if raw:
        return raw
    shot_index = 0
    cs = attrs.get("camera_subdivide")
    if isinstance(cs, dict):
        try:
            shot_index = int(cs.get("shot_index") or 0)
        except (TypeError, ValueError):
            shot_index = 0
    kadry = attrs.get("кадры")
    if isinstance(kadry, list) and kadry:
        if shot_index >= 1 and shot_index <= len(kadry):
            item = kadry[shot_index - 1]
            if isinstance(item, dict):
                piece = str(item.get("раскладка") or "").strip()
                if piece:
                    return piece
        for item in kadry:
            if isinstance(item, dict):
                piece = str(item.get("раскладка") or "").strip()
                if piece:
                    return piece
    return ""


def layout_is_freeze(layout: str) -> bool:
    text = layout or ""
    return bool(_START_MARK_RE.search(text) and _END_MARK_RE.search(text))


def layout_start_text(layout: str) -> str:
    match = _START_RE.search(layout or "")
    if not match:
        return ""
    return " ".join(match.group(1).split())


def layout_end_text(layout: str) -> str:
    match = _END_RE.search(layout or "")
    if not match:
        return ""
    return " ".join(match.group(1).split())


def frame_needs_end_still(frame: Any) -> bool:
    """Нужен конечный still (shot2): раскладка freeze или уже есть промт конца."""
    if layout_is_freeze(layout_text(frame)):
        return True
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict) and isinstance(frame, dict):
        attrs = frame.get("attrs") if isinstance(frame.get("attrs"), dict) else frame
    if not isinstance(attrs, dict):
        return False
    return bool(str(attrs.get(SHOT2_PROMPT_ATTR) or "").strip())


def _flag(frame: Any) -> None:
    with suppress(Exception):
        from app.services.vo_shot_expand import _flag_attrs

        _flag_attrs(frame)


def seed_end_still_prompt(frame: Any) -> bool:
    """Записать ``image_prompt_shot2`` из блока КОНЕЦ, если пусто.

    Не трогает ``image_prompt`` (shot1) — его пишет img_pr, skip_if_field
    не должен срабатывать из-за черновика.
    """
    attrs = getattr(frame, "attrs", None)
    attrs = {} if not isinstance(attrs, dict) else dict(attrs)
    existing = str(attrs.get(SHOT2_PROMPT_ATTR) or "").strip()
    layout = layout_text(frame)
    if not layout_is_freeze(layout):
        return bool(existing)
    if str(attrs.get(SHOT2_STATUS_ATTR) or "") == "skipped":
        attrs[SHOT2_STATUS_ATTR] = "image_prompt_ready"
    if existing:
        frame.attrs = attrs
        _flag(frame)
        return True
    end = layout_end_text(layout)
    if not end:
        frame.attrs = attrs
        _flag(frame)
        return False
    attrs[SHOT2_PROMPT_ATTR] = (
        f"Вертикальный кадр 9:16. Конечный стоп-кадр сцены. {end} "
        "Без читаемого текста на картинке."
    )
    if not str(attrs.get(SHOT2_STATUS_ATTR) or "").strip():
        attrs[SHOT2_STATUS_ATTR] = "image_prompt_ready"
    frame.attrs = attrs
    _flag(frame)
    return True


def frame_is_freeze(frame: Any) -> bool:
    return layout_is_freeze(layout_text(frame))


def video_end_still(
    scenes_dir: Path, frame_number: int, shot: int, *, frame: Any = None
) -> Path | None:
    """PNG конечного кадра для видео shot1. Shot2 сам уже конец — не дублируем.

    С ``frame`` — только для раскладки СТАРТ/КОНЕЦ: у legacy shot2 картинка —
    старт второго клипа, а не конец первого.
    """
    if int(shot) != 1:
        return None
    if frame is not None and not frame_is_freeze(frame):
        return None
    png = find_shot2_image(scenes_dir, int(frame_number))
    if png is not None and png.is_file():
        return png
    return None
