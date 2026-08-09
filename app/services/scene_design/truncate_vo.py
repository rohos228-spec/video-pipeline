"""Укоротить проект: оставить долю VO-диапазонов (для облегчения scene_design)."""

from __future__ import annotations

import math
from typing import Any

from loguru import logger
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, Frame, FrameEdge, FrameText, Project, PromptVersion
from app.services.scene_design.camera_expand import (
    _ATTR_KEY,
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

    После camera_subdivide группа = parent_uuid. Сворачиваем каждую
    оставшуюся группу в один Frame (склейка закадра, сумма времени),
    удаляем остальные кадры и связанные texts/edges/prompts/artifacts.
    ``script_text`` обрезаем под оставшийся закадр.
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

    # Упорядоченные VO-родители.
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
    keep_parents = parent_order[:keep_n]
    drop_parents = set(parent_order[keep_n:])

    kept_ids: set[int] = set()
    vo_parts: list[str] = []

    for pu in keep_parents:
        group = sorted(
            by_parent.get(pu) or [],
            key=lambda f: (f.sort_key is None, f.sort_key or 0.0, f.number or 0),
        )
        if not group:
            continue
        lead = next(
            (f for f in group if int(_subdivide_attr(f).get("shot_index") or 1) == 1),
            group[0],
        )
        texts = [
            (f.voiceover_text or "").strip()
            for f in group
            if (f.voiceover_text or "").strip()
        ]
        merged_vo = " ".join(texts).strip() or (lead.voiceover_text or "").strip()
        total_dur = 0.0
        any_dur = False
        for f in group:
            if f.duration_seconds is not None and float(f.duration_seconds) > 0:
                total_dur += float(f.duration_seconds)
                any_dur = True
        lead.voiceover_text = merged_vo
        if any_dur:
            lead.duration_seconds = round(total_dur, 2)
        # Аудиометки: оставляем только у lead (начало группы).
        # Хвост группы — метки сбрасываем вместе с удалением детей.
        attrs = dict(lead.attrs or {})
        attrs.pop(_ATTR_KEY, None)
        lead.attrs = attrs
        kept_ids.add(lead.id)
        if merged_vo:
            vo_parts.append(merged_vo)
        for f in group:
            if f.id != lead.id:
                # дети удалим ниже
                pass

    delete_ids = [f.id for f in frames if f.id not in kept_ids]
    # Также всё из drop_parents уже в delete_ids.

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

    # Обновить script_text под оставшийся закадр.
    new_script = "\n".join(vo_parts).strip()
    if new_script:
        project.script_text = new_script

    ordered = await _renumber(session, project)

    # Сбросить аудиометки у «хвоста» смысла: оставляем метки только если
    # start_ts укладывается в первые keep_ratio исходного таймлайна — проще:
    # обнулить end_ts у всех, кроме тех у кого был start_ts и они в kept
    # (метки детей уже удалены). Ничего не трогаем у kept lead.

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
