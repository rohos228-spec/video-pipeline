"""Общая механика: новая попытка шага заменяет его выход, не дописывает поверх.

Вызывать при входе в running (ручной ▶ и auto-advance). Повтор того же
running-такта (воркер, soft retry) сюда не попадает — статус уже running.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus


def step_code_for_running_status(status: ProjectStatus | None) -> str | None:
    """running-статус проекта → step_code, чей выход надо заменить."""
    if status is None:
        return None
    from app.orchestrator.node_registry import (
        NODE_TYPE_TO_STEP_CODE,
        RUNNING_TO_NODE_TYPE,
    )
    from app.services.excel_gpt_node import (
        EXCEL_GPT_STEP_CODE,
        slot_from_running_status,
    )

    if slot_from_running_status(status) is not None:
        return EXCEL_GPT_STEP_CODE
    node_type = RUNNING_TO_NODE_TYPE.get(status)
    if not node_type:
        return None
    return NODE_TYPE_TO_STEP_CODE.get(node_type)


async def _keep_fresh_sidecar_anim_pr(
    session: AsyncSession, project: Project
) -> dict[str, Any] | None:
    """Если sidecar уже получил видеопромты для тех же входов - не стирать (иначе повторный платный вызов).

    Записи sidecar снимаются при входе: следующий явный ▶ шага снова пересоберёт всё с нуля.
    Кадры без свежего результата sidecar очищаются как обычно (промт и shot2).
    """
    from sqlalchemy import select

    from app.models import Frame, FrameStatus
    from app.services.anim_pr_sidecar import take_fresh_sidecar_frames
    from app.services.plan_shot2 import SHOT2_VIDEO_PROMPT_ATTR

    frames = (
        await session.execute(select(Frame).where(Frame.project_id == project.id))
    ).scalars().all()
    kept = take_fresh_sidecar_frames(int(project.id), list(frames))
    if not kept:
        return None
    cleared = 0
    for fr in frames:
        if fr.number in kept:
            continue
        if (fr.animation_prompt or "").strip():
            fr.animation_prompt = None
            cleared += 1
        attrs = dict(fr.attrs or {})
        if attrs.pop(SHOT2_VIDEO_PROMPT_ATTR, None) is not None:
            fr.attrs = attrs
        if fr.status is FrameStatus.animation_prompt_ready:
            fr.status = FrameStatus.image_approved
    await session.flush()
    return {"anim_pr": {"kept_from_sidecar": len(kept), "frames_cleared": cleared}}


async def replace_step_outputs_on_enter(
    session: AsyncSession,
    project: Project,
    running_status: ProjectStatus,
) -> dict[str, Any]:
    """Стереть выход шага, в который сейчас входим, чтобы GPT/ген писал заново."""
    from app.services.reset_step import clear_step_outputs_for_rerun

    if getattr(project, "id", None) is None:
        return {}
    step_code = step_code_for_running_status(running_status)
    if not step_code:
        return {}
    if step_code == "anim_pr":
        try:
            kept = await _keep_fresh_sidecar_anim_pr(session, project)
        except Exception:  # noqa: BLE001
            logger.exception("[#{}] replace_step_outputs_on_enter: sidecar keep failed", project.id)
            kept = None
        if kept is not None:
            logger.info(
                "[#{}] replace_step_outputs_on_enter: anim_pr оставлен от sidecar {}",
                project.id,
                kept["anim_pr"],
            )
            return kept
    try:
        summary = await clear_step_outputs_for_rerun(
            session, project, step_code, force_wipe=True
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "[#{}] replace_step_outputs_on_enter: wipe {} failed",
            project.id,
            step_code,
        )
        return {"error": step_code}
    logger.info(
        "[#{}] replace_step_outputs_on_enter: status={} step={} wiped={}",
        project.id,
        running_status.value,
        step_code,
        list(summary.keys()),
    )
    return summary
