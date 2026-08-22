"""Дробление VO-ячейки на визуальные шоты для группы script_frames_qc.

1 ячейка закадра = 1 сцена. Кадры сцены несут фрагменты текста;
сумма фрагментов = ячейка. Не пишет новый закадр и не трогает chrono_dyn.
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


async def expand_vo_cells_into_shots(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
) -> tuple[list[Frame], dict[str, Any]]:
    """Вставить шоты в VO-родителей и раздать фрагменты закадра."""
    report: dict[str, Any] = {
        "skipped": False,
        "parents": 0,
        "inserted": 0,
        "frames_before": len(frames),
        "frames_after": len(frames),
    }
    if already_subdivided(frames):
        report["skipped"] = True
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
        if need <= 1:
            attrs = dict(parent.attrs or {})
            attrs[_ATTR_KEY] = {
                "role": "vo_parent",
                "parent_uuid": parent_uuid,
                "shot_index": 1,
                "shots_in_beat": 1,
            }
            parent.attrs = attrs
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
        vo_parts = split_text_into_parts(original_vo, need)
        for i, fr in enumerate(group):
            fr.voiceover_text = vo_parts[i]
            if i == 0:
                fr.start_ts = start_ts
                fr.end_ts = end_ts
            else:
                fr.start_ts = None
                fr.end_ts = None
            fr.duration_seconds = part_sec
            attrs = dict(fr.attrs or {})
            attrs[_ATTR_KEY] = {
                "role": "vo_parent" if i == 0 else "shot",
                "parent_uuid": parent_uuid,
                "shot_index": i + 1,
                "shots_in_beat": need,
            }
            fr.attrs = attrs

    await session.flush()
    ordered = await renumber_frames_by_sort_key(session, project)
    report["parents"] = len(parents)
    report["inserted"] = inserted
    report["frames_after"] = len(ordered)
    logger.info(
        "[#{}] vo_shot_expand: parents={} inserted={} frames {}→{}",
        project.id,
        report["parents"],
        inserted,
        report["frames_before"],
        report["frames_after"],
    )
    return ordered, report
