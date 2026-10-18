"""Укоротить проект: оставить долю VO-кадров как есть (без перекладки текста)."""

from __future__ import annotations

import math
from typing import Any

from loguru import logger
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, Frame, FrameEdge, FrameText, Project, PromptVersion
from app.services.scene_design.camera_expand import (
    _subdivide_attr,
    renumber_frames_by_sort_key as _renumber,
)


async def truncate_vo_to_ratio(
    session: AsyncSession,
    project: Project,
    *,
    keep_ratio: float = 0.3,
) -> dict[str, Any]:
    """Оставить первые ``keep_ratio`` VO-диапазонов по хронологии.

    Текст в ячейках **не трогаем и не склеиваем**. Если кадры уже
    subdivided — оставляем все шоты выбранных родительских диапазонов.
    Удаляем только хвост. ``script_text`` = склейка оставшихся VO через ``\\n``.
    """
    keep_ratio = min(1.0, max(0.05, float(keep_ratio)))
    frames = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        ).scalars()
    )
    if not frames:
        return {"kept_frames": 0, "deleted_frames": 0}

    parent_order: list[str] = []
    by_parent: dict[str, list[Frame]] = {}
    seen: set[str] = set()
    for fr in frames:
        meta = _subdivide_attr(fr)
        pu = str(meta.get("parent_uuid") or fr.uuid or "").strip()
        if not pu:
            continue
        by_parent.setdefault(pu, []).append(fr)
        if pu not in seen:
            seen.add(pu)
            parent_order.append(pu)

    if not parent_order:
        parent_order = [f.uuid for f in frames if f.uuid]
        by_parent = {f.uuid: [f] for f in frames if f.uuid}

    keep_n = max(1, int(math.ceil(len(parent_order) * keep_ratio)))
    drop_parents = set(parent_order[keep_n:])

    kept_ids: set[int] = set()
    for pu in parent_order[:keep_n]:
        for f in by_parent.get(pu) or []:
            kept_ids.add(f.id)

    delete_ids = [f.id for f in frames if f.id not in kept_ids]

    if delete_ids:
        await session.execute(
            delete(FrameText).where(FrameText.frame_id.in_(delete_ids))
        )
        await session.execute(
            delete(PromptVersion).where(PromptVersion.frame_id.in_(delete_ids))
        )
        await session.execute(
            delete(Artifact).where(Artifact.frame_id.in_(delete_ids))
        )
        await session.execute(
            delete(FrameEdge).where(
                FrameEdge.project_id == project.id,
                or_(
                    FrameEdge.from_frame_id.in_(delete_ids),
                    FrameEdge.to_frame_id.in_(delete_ids),
                ),
            )
        )
        await session.execute(delete(Frame).where(Frame.id.in_(delete_ids)))
        await session.flush()

    ordered = await _renumber(session, project)
    vo_parts = [
        (f.voiceover_text or "").strip()
        for f in ordered
        if (f.voiceover_text or "").strip()
    ]
    if vo_parts:
        project.script_text = "\n".join(vo_parts)

    report = {
        "parents_total": len(parent_order),
        "parents_kept": keep_n,
        "parents_dropped": len(drop_parents),
        "kept_frames": len(ordered),
        "deleted_frames": len(delete_ids),
        "script_len": len(project.script_text or ""),
        "keep_ratio": keep_ratio,
    }
    logger.info("[#{}] truncate_vo: {}", project.id, report)
    return report
