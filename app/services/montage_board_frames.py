"""Вставка / удаление кадров и правка закадра с панели монтажа."""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, Frame, FrameEdge, FrameStatus, FrameText, Project, PromptVersion
from app.services.db_v2 import insert_frame_after
from app.services.montage_coverage_ops import (
    _children_of,
    _load_frames,
    delete_coverage_child,
)
from app.services.scene_design.camera_expand import renumber_frames_by_sort_key
from app.services.vo_shot_expand import _set_cs, is_shot_child


async def upsert_frame_voiceover(session: AsyncSession, frame: Frame, text: str) -> None:
    """Frame.voiceover_text + FrameText(kind=voiceover)."""
    vo = (text or "").strip()
    frame.voiceover_text = vo
    existing = (
        await session.execute(
            select(FrameText).where(
                FrameText.frame_id == frame.id,
                FrameText.kind == "voiceover",
            )
        )
    ).scalars().first()
    if not vo:
        if existing is not None:
            await session.delete(existing)
        return
    if existing is None:
        session.add(
            FrameText(
                project_id=int(frame.project_id),
                frame_id=int(frame.id),
                kind="voiceover",
                text=vo,
            )
        )
        return
    existing.text = vo


async def set_montage_voiceover(
    session: AsyncSession,
    project: Project,
    frame_id: int,
    text: str,
) -> Frame:
    frame = await session.get(Frame, frame_id)
    if frame is None or int(frame.project_id) != int(project.id):
        raise RuntimeError(f"кадр {frame_id} не найден")
    await upsert_frame_voiceover(session, frame, text)
    await session.flush()
    logger.info(
        "montage voiceover #{} frame {} → {} симв.",
        project.id,
        frame.number,
        len(frame.voiceover_text or ""),
    )
    return frame


async def insert_montage_frame(
    session: AsyncSession,
    project: Project,
    *,
    after_frame_id: int | None,
    voiceover: str = "",
) -> Frame:
    """Новый VO-родитель после кадра (или в начало). Номера — по sort_key."""
    fr = await insert_frame_after(
        session,
        project,
        after_frame_id=after_frame_id,
    )
    uid = str(fr.uuid or "").strip()
    vo = (voiceover or "").strip()
    fr.status = FrameStatus.planned
    fr.voiceover_text = vo
    _set_cs(fr, role="vo_parent", parent_uuid=uid)
    await session.flush()
    await upsert_frame_voiceover(session, fr, vo)
    await renumber_frames_by_sort_key(session, project)
    await session.flush()
    live = await session.get(Frame, fr.id)
    out = live if live is not None else fr
    logger.info(
        "montage insert #{} after={} → frame {} uuid={}",
        project.id,
        after_frame_id,
        out.number,
        out.uuid,
    )
    return out


async def _drop_frame_rows(
    session: AsyncSession,
    project: Project,
    frames_to_drop: list[Frame],
) -> None:
    drop_ids = [int(fr.id) for fr in frames_to_drop if getattr(fr, "id", None)]
    if not drop_ids:
        return
    await session.execute(
        update(Artifact).where(Artifact.frame_id.in_(drop_ids)).values(frame_id=None)
    )
    await session.execute(delete(PromptVersion).where(PromptVersion.frame_id.in_(drop_ids)))
    await session.execute(delete(FrameText).where(FrameText.frame_id.in_(drop_ids)))
    await session.execute(
        delete(FrameEdge).where(
            FrameEdge.project_id == project.id,
            or_(
                FrameEdge.from_frame_id.in_(drop_ids),
                FrameEdge.to_frame_id.in_(drop_ids),
            ),
        )
    )
    for fr in frames_to_drop:
        await session.delete(fr)
    await session.flush()


async def delete_montage_frame(
    session: AsyncSession,
    project: Project,
    frame_id: int,
) -> dict[str, Any]:
    frames = await _load_frames(session, int(project.id))
    frame = next((fr for fr in frames if int(fr.id) == int(frame_id)), None)
    if frame is None:
        raise RuntimeError(f"кадр {frame_id} не найден")
    number = int(frame.number)
    if is_shot_child(frame):
        await delete_coverage_child(session, project, frame, frames)
        await renumber_frames_by_sort_key(session, project)
        logger.info("montage delete child #{} frame {}", project.id, number)
        return {"ok": True, "frame_id": frame_id, "number": number, "deleted": 1}

    kids = _children_of(frames, frame)
    drop = [frame, *kids]
    await _drop_frame_rows(session, project, drop)
    await renumber_frames_by_sort_key(session, project)
    logger.info(
        "montage delete cell #{} frame {} (+{} children)",
        project.id,
        number,
        len(kids),
    )
    return {
        "ok": True,
        "frame_id": frame_id,
        "number": number,
        "deleted": len(drop),
    }
