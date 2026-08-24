"""Визуальные шоты внутри ячейки разбивки.

Frame.voiceover_text = результат ноды разбивки. Не пишем, не режем.
Копия ячейки режется по шотам в attrs.camera_subdivide.vo_shot —
так же, как раньше резался закадр (split_text_into_parts).
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.db_v2 import insert_frame_after
from app.services.scene_design.camera_expand import (
    already_subdivided,
    renumber_frames_by_sort_key,
    split_text_into_parts,
)

_ATTR_KEY = "camera_subdivide"
_VO_SHOT_KEY = "vo_shot"
_CLAUSE_RE = re.compile(r"(?<=[.!?…])\s+")
_VO_CHARS_PER_SEC = 14.0
_MIN_SEC = 2.0
_MAX_SHOTS = 3


def shots_needed_for_vo(text: str) -> int:
    """Сколько визуальных шотов на ячейку: по фразам, не больше 3."""
    raw = (text or "").strip()
    if not raw:
        return 1
    clauses = [p.strip() for p in _CLAUSE_RE.split(raw) if p.strip()]
    n = len(clauses) if len(clauses) > 1 else 1
    if n <= 1 and len(raw.split()) >= 8:
        n = 2
    return max(1, min(_MAX_SHOTS, n))


def vo_duration_sec(text: str, *, shots: int = 1) -> float:
    """Длительность ячейки по 14 символам/сек; на шот не короче 2с."""
    chars = len((text or "").strip())
    total = max(_MIN_SEC * max(1, shots), chars / _VO_CHARS_PER_SEC if chars else _MIN_SEC)
    return round(total, 2)


def _cs(frame: Any) -> dict[str, Any]:
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict):
        return {}
    raw = attrs.get(_ATTR_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _flag_attrs(frame: Any) -> None:
    state = getattr(frame, "_sa_instance_state", None)
    if state is None:
        return
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(frame, "attrs")


def _set_cs(frame: Any, **fields: Any) -> None:
    attrs = dict(getattr(frame, "attrs", None) or {})
    cs = dict(attrs.get(_ATTR_KEY) or {})
    cs.update(fields)
    attrs[_ATTR_KEY] = cs
    frame.attrs = attrs
    _flag_attrs(frame)


def repair_split_vo_on_parents(frames: list[Any]) -> int:
    """Вернуть полный закадр разбивки на родителя, с детей снять.

    Если expand уже нарезал Frame.voiceover_text — склеить обратно.
    Не генерирует новый текст.
    """
    groups: dict[str, list[Any]] = {}
    for fr in frames:
        cs = _cs(fr)
        parent = str(cs.get("parent_uuid") or getattr(fr, "uuid", "") or "").strip()
        if not parent:
            continue
        groups.setdefault(parent, []).append(fr)
    repaired = 0
    for members in groups.values():
        members.sort(
            key=lambda m: int(_cs(m).get("shot_index") or 0)
        )
        if len(members) <= 1:
            continue
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            members[0],
        )
        parts = [(getattr(m, "voiceover_text", None) or "").strip() for m in members]
        child_parts = [
            (getattr(m, "voiceover_text", None) or "").strip()
            for m in members
            if m is not parent
        ]
        if not any(child_parts):
            continue
        unique = {p for p in parts if p}
        if len(unique) == 1:
            joined = next(iter(unique))
        else:
            joined = " ".join(p for p in parts if p)
        if joined:
            parent.voiceover_text = joined
        for m in members:
            if m is not parent:
                m.voiceover_text = ""
        repaired += 1
    return repaired


def apply_vo_shot_cuts(frames: list[Any]) -> int:
    """Дубликат ячейки разбивки режем по шотам. voiceover_text не трогаем."""
    groups: dict[str, list[Any]] = {}
    for fr in frames:
        cs = _cs(fr)
        parent = str(cs.get("parent_uuid") or getattr(fr, "uuid", "") or "").strip()
        if not parent:
            continue
        groups.setdefault(parent, []).append(fr)
    updated = 0
    for members in groups.values():
        members.sort(key=lambda m: int(_cs(m).get("shot_index") or 0))
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            members[0],
        )
        original = (getattr(parent, "voiceover_text", None) or "").strip()
        if not original:
            continue
        parts = split_text_into_parts(original, len(members))
        for i, fr in enumerate(members):
            piece = parts[i] if i < len(parts) else ""
            if str(_cs(fr).get(_VO_SHOT_KEY) or "") == piece:
                continue
            _set_cs(fr, **{_VO_SHOT_KEY: piece})
            updated += 1
    return updated


async def expand_vo_cells_into_shots(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
) -> tuple[list[Frame], dict[str, Any]]:
    """Вставить визуальные шоты. Закадр разбивки не меняем; режем копию."""
    report: dict[str, Any] = {
        "skipped": False,
        "parents": 0,
        "inserted": 0,
        "repaired_vo": 0,
        "vo_shot_cuts": 0,
        "frames_before": len(frames),
        "frames_after": len(frames),
    }
    if already_subdivided(frames):
        report["skipped"] = True
        report["repaired_vo"] = repair_split_vo_on_parents(frames)
        report["vo_shot_cuts"] = apply_vo_shot_cuts(frames)
        return frames, report

    parents = [f for f in frames if f.uuid]
    parents.sort(key=lambda f: (f.sort_key is None, f.sort_key or 0.0, f.number or 0))
    inserted = 0
    for parent in reversed(parents):
        original_vo = parent.voiceover_text or ""
        need = shots_needed_for_vo(original_vo)
        total_sec = vo_duration_sec(original_vo, shots=need)
        part_sec = round(total_sec / need, 2)
        start_ts = parent.start_ts
        end_ts = parent.end_ts
        parent_uuid = parent.uuid
        vo_parts = split_text_into_parts(original_vo, need)
        if need <= 1:
            _set_cs(
                parent,
                role="vo_parent",
                parent_uuid=parent_uuid,
                shot_index=1,
                shots_in_beat=1,
                vo_shot=vo_parts[0] if vo_parts else original_vo,
            )
            if not parent.duration_seconds:
                parent.duration_seconds = part_sec
            continue

        children: list[Frame] = []
        after_id = parent.id
        for _ in range(need - 1):
            child = await insert_frame_after(
                session, project, after_frame_id=after_id
            )
            children.append(child)
            after_id = child.id
            inserted += 1

        group = [parent, *children]
        parent.voiceover_text = original_vo
        for i, fr in enumerate(group):
            if i == 0:
                fr.start_ts = start_ts
                fr.end_ts = end_ts
            else:
                fr.start_ts = None
                fr.end_ts = None
                fr.voiceover_text = ""
            fr.duration_seconds = part_sec
            _set_cs(
                fr,
                role="vo_parent" if i == 0 else "shot",
                parent_uuid=parent_uuid,
                shot_index=i + 1,
                shots_in_beat=need,
                vo_shot=vo_parts[i],
            )

    await session.flush()
    ordered = await renumber_frames_by_sort_key(session, project)
    report["parents"] = len(parents)
    report["inserted"] = inserted
    report["vo_shot_cuts"] = apply_vo_shot_cuts(ordered)
    report["frames_after"] = len(ordered)
    logger.info(
        "[#{}] vo_shot_expand: parents={} inserted={} vo_shot_cuts={} frames {}→{}",
        project.id,
        report["parents"],
        inserted,
        report["vo_shot_cuts"],
        report["frames_before"],
        report["frames_after"],
    )
    return ordered, report
