"""Меню съёмки: 1 ячейка закадра = 1 блок, шоты камеры — внутри.

Не склеивает соседние VO. Кадры без закадра — только шоты предыдущей ячейки.
Источник правды — DB frames, не rewrite чек-ноды.
SET_xx показываем с человеческим именем из camera_sets.md.
"""

from __future__ import annotations

import re
from functools import lru_cache
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


@lru_cache(maxsize=1)
def _set_catalog() -> dict[str, str]:
    """SET_01 → «Открытие места» из prompts/scene_design/camera_sets.md."""
    from app.project_root import find_project_root

    path = find_project_root() / "prompts" / "scene_design" / "camera_sets.md"
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for m in re.finditer(r"^##\s+(SET_\d+)\s*«([^»]+)»", text, re.M):
        out[m.group(1)] = m.group(2).strip()
    return out


def set_human_name(set_id: str) -> str:
    """SET_08 → «SET_08 · Документ/свидетельство» (без id в каталоге — как есть)."""
    sid = set_id.strip().upper()
    if not re.fullmatch(r"SET_?\d+", sid):
        return set_id
    sid = f"SET_{int(re.search(r'\d+', sid).group()):02d}"  # noqa: B905
    name = _set_catalog().get(sid)
    return f"{sid} · {name}" if name else set_id


def _shot_set(frame: dict[str, Any]) -> str:
    cs = _camera_subdivide(frame)
    nab = cs.get("набор")
    if isinstance(nab, dict):
        nab = nab.get("id") or nab.get("name") or nab.get("title")
    raw = _text(nab) or frame_field(frame, "place", "shot01_bg", "shot01_props")
    return set_human_name(raw) if raw else ""


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


def _all_fields(frame: dict[str, Any]) -> dict[str, str]:
    """Все поля кадра (колонки + attrs) — для пользовательских строк меню."""
    out: dict[str, str] = {}
    for key in (
        "number",
        "voiceover_text",
        "image_prompt",
        "animation_prompt",
        "duration_seconds",
        "status",
    ):
        val = _text(frame.get(key))
        if val:
            out[key] = val[:2000]
    attrs = frame.get("attrs")
    if isinstance(attrs, dict):
        for k, v in attrs.items():
            s = _text(v if not isinstance(v, (dict, list)) else str(v))
            if s:
                out[str(k)] = s[:2000]
    return out


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
        "all": _all_fields(frame),
    }


def group_vo_cells(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1 непустой закадр = новая ячейка. Пустой VO только присоединяется как шот."""
    cells: list[dict[str, Any]] = []
    current_frames: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current_frames
        if not current_frames:
            return
        cell_index = len(cells) + 1
        parent = current_frames[0]
        shots = [
            _shot_dto(fr, cell_index, i)
            for i, fr in enumerate(current_frames, start=1)
        ]
        duration = round(sum(s["duration_sec"] for s in shots), 2)
        vo = _vo_text(parent)
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

    for frame in _order_frames(frames):
        vo = _vo_text(frame)
        if vo:
            flush()
            current_frames = [frame]
            continue
        if current_frames:
            current_frames.append(frame)
            continue
        # Шоты без родителя в начале ленты — отдельная ячейка без закадра.
        current_frames = [frame]
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


# ── Правки из меню: ячейки и поля шотов ──────────────────────────────

# Редактируемые поля шота → колонка Frame или ключ attrs.
_FIELD_TO_COLUMN = {
    "img_prompt": "image_prompt",
    "video_prompt": "animation_prompt",
}
_FIELD_TO_ATTR = {
    "action": "shot01_action",
    "characters": "characters",
    "stitch": "scene_transition",
    "scene": "id_scene",
}


async def edit_cell_voiceover(
    session: Any,
    project_id: int,
    parent_uuid: str,
    text: str,
) -> dict[str, Any]:
    """Правка закадра ячейки (родительский кадр) из меню съёмки."""
    from sqlalchemy import select

    from app.models import Frame

    fr = (
        await session.execute(
            select(Frame).where(
                Frame.project_id == project_id, Frame.uuid == parent_uuid
            )
        )
    ).scalars().first()
    if fr is None:
        raise ValueError(f"кадр {parent_uuid!r} не найден")
    fr.voiceover_text = text.strip()
    await session.commit()
    return {"ok": True, "uuid": parent_uuid, "chars": len(fr.voiceover_text)}


async def edit_shot_field(
    session: Any,
    project_id: int,
    frame_uuid: str,
    field: str,
    value: str,
) -> dict[str, Any]:
    """Правка поля шота (колонка или attrs) из меню съёмки."""
    from sqlalchemy import select

    from app.models import Frame

    fr = (
        await session.execute(
            select(Frame).where(
                Frame.project_id == project_id, Frame.uuid == frame_uuid
            )
        )
    ).scalars().first()
    if fr is None:
        raise ValueError(f"кадр {frame_uuid!r} не найден")
    if field in _FIELD_TO_COLUMN:
        setattr(fr, _FIELD_TO_COLUMN[field], value.strip() or None)
    elif field in _FIELD_TO_ATTR:
        attrs = dict(fr.attrs or {})
        attrs[_FIELD_TO_ATTR[field]] = value.strip()
        fr.attrs = attrs
    else:
        raise ValueError(f"поле {field!r} не редактируется из меню")
    await session.commit()
    return {"ok": True, "uuid": frame_uuid, "field": field}


async def add_cell(
    session: Any,
    project: Any,
    *,
    before_index: int | None = None,
    voiceover: str = "",
) -> dict[str, Any]:
    """Новая пустая ячейка перед ячейкой N (или в конец) — дробный sort_key."""
    from sqlalchemy import select

    from app.models import Frame
    from app.services.db_v2 import sort_key_between
    from app.services.scene_design.camera_expand import renumber_frames_by_sort_key

    frames = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
    cells = group_vo_cells([_frame_dump(fr) for fr in frames])

    before_sk: float | None = None
    after_sk: float | None = None
    if before_index is None or before_index > len(cells):
        # в конец
        after_sk = float(frames[-1].sort_key or 0.0) if frames else None
    else:
        target = cells[max(0, before_index - 1)]
        before_frame = next(
            (fr for fr in frames if str(fr.uuid) == str(target["parent_uuid"])),
            None,
        )
        before_sk = (
            float(before_frame.sort_key or 0.0) if before_frame is not None else None
        )
        # предыдущий кадр перед целевым
        if before_frame is not None:
            idx = frames.index(before_frame)
            if idx > 0:
                after_sk = float(frames[idx - 1].sort_key or 0.0)
        else:
            after_sk = None

    max_number = max((int(fr.number or 0) for fr in frames), default=0)
    import uuid as _uuid

    fr = Frame(
        project_id=project.id,
        number=max_number + 1,
        voiceover_text=voiceover.strip(),
        status="planned",
        uuid=_uuid.uuid4().hex[:12],
        sort_key=sort_key_between(after_sk, before_sk),
        attrs={},
    )
    session.add(fr)
    await session.flush()
    await renumber_frames_by_sort_key(session, project)
    await session.commit()
    return {"ok": True, "uuid": fr.uuid, "number": fr.number}


def _frame_dump(fr: Any) -> dict[str, Any]:
    return {
        "id": fr.id,
        "uuid": fr.uuid,
        "number": fr.number,
        "voiceover_text": fr.voiceover_text,
        "duration_seconds": fr.duration_seconds,
        "image_prompt": fr.image_prompt,
        "animation_prompt": fr.animation_prompt,
        "attrs": fr.attrs or {},
        "sort_key": fr.sort_key,
    }
