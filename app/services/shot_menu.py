"""Меню съёмки: 1 ячейка закадра = 1 блок, шоты камеры — внутри.

Не склеивает соседние VO. Кадры без закадра — только шоты предыдущей ячейки.
Источник правды — DB frames, не rewrite чек-ноды.
"""

from __future__ import annotations

from typing import Any

SHOT_MENU_TRACKS: tuple[dict[str, Any], ...] = (
    {"key": "vo", "label": "Закадр", "pinned": True},
    {"key": "action", "label": "Действие", "pinned": False},
    {"key": "cam", "label": "Крупность · движение", "pinned": False},
    {"key": "set", "label": "SET", "pinned": False},
    {"key": "characters", "label": "Персонажи", "pinned": False},
    {"key": "stitch", "label": "Стык", "pinned": False},
    {"key": "scene", "label": "Сцена", "pinned": False},
    {"key": "img_prompt", "label": "Промт картинки", "pinned": False},
    {"key": "video_prompt", "label": "Промт видео", "pinned": False},
    {"key": "frame_no", "label": "Номер кадра", "pinned": False},
)

DEFAULT_TRACK_KEYS: tuple[str, ...] = ("vo", "action", "cam", "set")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(*values: Any) -> str:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def frame_field(frame: dict[str, Any], *keys: str) -> str:
    """Значение с карточки: колонка кадра или ``attrs`` (оба варианта дампа)."""
    attrs = _as_dict(frame.get("attrs"))
    for key in keys:
        found = _text(frame.get(key), attrs.get(key))
        if found:
            return found
    return ""


def _duration_sec(frame: dict[str, Any]) -> float:
    raw = frame.get("duration_seconds")
    if raw is None:
        raw = _as_dict(frame.get("attrs")).get("duration_seconds")
    try:
        val = float(raw or 0)
    except (TypeError, ValueError):
        return 0.0
    return val if val > 0 else 0.0


def _camera_subdivide(frame: dict[str, Any]) -> dict[str, Any]:
    attrs = _as_dict(frame.get("attrs"))
    cs = frame.get("camera_subdivide")
    if not isinstance(cs, dict):
        cs = attrs.get("camera_subdivide")
    return cs if isinstance(cs, dict) else {}


def _shot_cam(frame: dict[str, Any]) -> str:
    cs = _camera_subdivide(frame)
    notes = frame_field(frame, "shot01_notes")
    size = _text(cs.get("крупность"), cs.get("size"))
    move = _text(cs.get("движение"), cs.get("move"))
    from_cs = " · ".join(p for p in (size, move) if p)
    return _text(notes, from_cs)


def _shot_set(frame: dict[str, Any]) -> str:
    cs = _camera_subdivide(frame)
    nab = cs.get("набор")
    if isinstance(nab, dict):
        nab = nab.get("id") or nab.get("name") or nab.get("title")
    return _text(
        nab,
        frame_field(frame, "place", "shot01_bg", "shot01_props"),
    )


def _vo_text(frame: dict[str, Any]) -> str:
    return frame_field(frame, "voiceover_text", "закадр")


def _active_prompt(frame: dict[str, Any], kind: str, fallback_key: str) -> str:
    prompts = frame.get("prompts")
    if isinstance(prompts, list):
        for p in prompts:
            if not isinstance(p, dict):
                continue
            if str(p.get("kind") or "") != kind:
                continue
            if not p.get("is_active"):
                continue
            text = _text(p.get("text"))
            if text:
                return text
    return frame_field(frame, fallback_key)


def _order_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(fr: dict[str, Any]) -> tuple[float, int]:
        number = int(fr.get("number") or 0)
        sort_key = fr.get("sort_key")
        try:
            sk = float(sort_key) if sort_key is not None else float(number) * 10.0
        except (TypeError, ValueError):
            sk = float(number) * 10.0
        return (sk, number)

    return sorted(frames, key=key)


def _shot_label(cell_index: int, shot_index: int) -> str:
    return f"{cell_index}.{shot_index}"


def _format_clock(seconds: float) -> str:
    total = max(0.0, float(seconds))
    minutes = int(total // 60)
    rest = total - minutes * 60
    if rest == int(rest):
        return f"{minutes}:{int(rest):02d}"
    return f"{minutes}:{rest:04.1f}"


def _shot_dto(frame: dict[str, Any], cell_index: int, shot_index: int) -> dict[str, Any]:
    vo = _vo_text(frame)
    return {
        "id": frame.get("id"),
        "uuid": frame.get("uuid"),
        "number": frame.get("number"),
        "label": _shot_label(cell_index, shot_index),
        "duration_sec": round(_duration_sec(frame), 2),
        "image_url": frame.get("image_url"),
        "voiceover_in_shot": vo if vo else "—",
        "fields": {
            "action": frame_field(frame, "shot01_action", "main_action", "действие"),
            "cam": _shot_cam(frame),
            "set": _shot_set(frame),
            "characters": frame_field(frame, "characters", "персонажи"),
            "stitch": frame_field(
                frame, "scene_transition", "shot01_transition", "edit_type"
            ),
            "scene": frame_field(frame, "shot01_id_scene", "id_scene", "scene_sense"),
            "img_prompt": _active_prompt(frame, "img", "image_prompt"),
            "video_prompt": _active_prompt(frame, "video", "animation_prompt"),
            "frame_no": str(frame.get("number") or ""),
        },
    }


def _subdivide_parent_uuid(frame: dict[str, Any]) -> str:
    """parent_uuid из attrs.camera_subdivide (SET-дробь); без метки = ''."""
    attrs = frame.get("attrs")
    if not isinstance(attrs, dict):
        return ""
    cs = attrs.get("camera_subdivide")
    if not isinstance(cs, dict):
        return ""
    return str(cs.get("parent_uuid") or "").strip()


def group_vo_cells(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ячейка = сцена: кадры одной VO-ячейки группируются по parent_uuid
    (camera_subdivide). Без метки — старое правило: непустой закадр = новая
    ячейка, пустой VO присоединяется как шот. Текст ячейки = сумма фрагментов
    её кадров (после дроби каждый кадр несёт свой кусок закадра)."""
    cells: list[dict[str, Any]] = []
    current_frames: list[dict[str, Any]] = []
    current_pu = ""

    def flush() -> None:
        nonlocal current_frames, current_pu
        if not current_frames:
            return
        cell_index = len(cells) + 1
        parent = current_frames[0]
        shots = [
            _shot_dto(fr, cell_index, i)
            for i, fr in enumerate(current_frames, start=1)
        ]
        duration = round(sum(s["duration_sec"] for s in shots), 2)
        vo = " ".join(
            t for t in (_vo_text(fr) for fr in current_frames) if t
        ).strip()
        loc = _shot_set(parent)
        items = frame_field(parent, "shot01_props", "items")
        hide = frame_field(parent, "hide", "не_показывать")
        title_src = vo or frame_field(parent, "meaning") or f"Ячейка {cell_index}"
        title = title_src if len(title_src) <= 72 else title_src[:71].rstrip() + "…"
        cells.append(
            {
                "index": cell_index,
                "parent_id": parent.get("id"),
                "parent_uuid": parent.get("uuid"),
                "title": title,
                "voiceover": vo,
                "duration_sec": duration,
                "loc": loc,
                "items": items,
                "hide": hide,
                "layer": " · ".join(
                    p
                    for p in (
                        "Слой факт",
                        loc,
                        items,
                        f"не показывать: {hide}" if hide else "",
                    )
                    if p
                ),
                "shots": shots,
            }
        )
        current_frames = []
        current_pu = ""

    for frame in _order_frames(frames):
        vo = _vo_text(frame)
        pu = _subdivide_parent_uuid(frame)
        if pu:
            # Кадр SET-группы: та же ячейка, что и родитель.
            if current_frames and pu == current_pu:
                current_frames.append(frame)
                continue
            flush()
            current_frames = [frame]
            current_pu = pu
            continue
        if vo:
            flush()
            current_frames = [frame]
            current_pu = ""
            continue
        if current_frames:
            current_frames.append(frame)
            continue
        # Шоты без родителя в начале ленты — отдельная ячейка без закадра.
        current_frames = [frame]
        current_pu = ""
    flush()
    return cells


def build_shot_menu(
    frames: list[dict[str, Any]],
    *,
    scenes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """DTO ленты меню съёмки."""
    del scenes  # сцены DB не склеивают VO-ячейки; оставляем параметр для API.
    cells = group_vo_cells(frames)
    shot_count = sum(len(c["shots"]) for c in cells)
    total_sec = round(sum(float(c["duration_sec"] or 0) for c in cells), 2)
    vo_chars = sum(len(str(c.get("voiceover") or "")) for c in cells)
    return {
        "tracks": [dict(t) for t in SHOT_MENU_TRACKS],
        "default_tracks": list(DEFAULT_TRACK_KEYS),
        "cells": cells,
        "summary": {
            "vo_cells": len(cells),
            "shots": shot_count,
            "duration_sec": total_sec,
            "duration_clock": _format_clock(total_sec),
            "vo_chars": vo_chars,
        },
    }
