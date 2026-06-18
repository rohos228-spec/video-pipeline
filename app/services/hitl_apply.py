"""Побочные эффекты HITL-решений (общие для веб-API и Telegram).

Веб раньше только писал `HITLRequest.decision` в БД; без смены
`Project.status` пайплайн зависал на hero и др. Здесь зеркалим логику
из `bot.py` / `auto_advance._next_status_after_hero_approve`.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Frame,
    FrameStatus,
    HITLDecision,
    HITLKind,
    HITLRequest,
    Project,
    ProjectStatus,
)
from app.orchestrator.auto_advance import (
    TRANSITIONS,
    _apply_approve,
    _apply_regen,
    _apply_reject,
    _next_status_after_hero_approve,
    get_latest_hitl,
)
from app.services.auto_review import ReviewResult


def _action_for_decision(decision: HITLDecision) -> str:
    if decision is HITLDecision.approved:
        return "approve"
    if decision is HITLDecision.regenerate:
        return "regen"
    if decision is HITLDecision.rejected:
        return "reject"
    return ""


_FRAME_VISUAL_KINDS = frozenset({HITLKind.approve_images, HITLKind.approve_videos})


async def _pending_visual_hitl_count(
    session: AsyncSession, project_id: int, kind: HITLKind
) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(HITLRequest)
            .where(
                HITLRequest.project_id == project_id,
                HITLRequest.kind == kind,
                HITLRequest.decision == HITLDecision.pending,
            )
        )
    ).scalar_one()


async def _maybe_advance_visual_ready(
    session: AsyncSession, project: Project, kind: HITLKind
) -> None:
    """Когда все per-frame HITL закрыты — перевести проект на следующий шаг."""
    if await _pending_visual_hitl_count(session, project.id, kind) > 0:
        return
    ready_map = {
        HITLKind.approve_images: ProjectStatus.images_ready,
        HITLKind.approve_videos: ProjectStatus.videos_ready,
    }
    ready = ready_map.get(kind)
    if ready is None or project.status != ready:
        return
    transition = TRANSITIONS.get(ready)
    if transition is None:
        return
    hitl = await get_latest_hitl(session, project.id, kind)
    await _apply_approve(session, project, hitl, transition, bot=None)


async def _apply_frame_visual_hitl(
    session: AsyncSession,
    project: Project,
    req: HITLRequest,
    decision: HITLDecision,
) -> bool:
    """Решение по одному кадру — не ставим весь проект на паузу."""
    if req.frame_id is None or req.kind not in _FRAME_VISUAL_KINDS:
        return False
    frame = await session.get(Frame, req.frame_id)
    if frame is None:
        return True
    if req.kind is HITLKind.approve_images:
        if decision is HITLDecision.approved:
            frame.status = FrameStatus.image_approved
        elif decision is HITLDecision.rejected:
            frame.status = FrameStatus.failed
        elif decision in (HITLDecision.regenerate, HITLDecision.edit_prompt):
            frame.status = FrameStatus.image_prompt_ready
    else:
        if decision is HITLDecision.approved:
            frame.status = FrameStatus.video_approved
        elif decision is HITLDecision.rejected:
            frame.status = FrameStatus.failed
        elif decision in (HITLDecision.regenerate, HITLDecision.edit_prompt):
            frame.status = FrameStatus.animation_prompt_ready
    await session.flush()
    if not getattr(project, "auto_mode", False):
        await _maybe_advance_visual_ready(session, project, req.kind)
    return True


async def apply_hitl_side_effects(
    session: AsyncSession,
    req: HITLRequest,
    decision: HITLDecision,
) -> None:
    """Обновить статус проекта после решения по HITL (если нужно)."""
    if decision is HITLDecision.pending:
        return

    project = await session.get(Project, req.project_id)
    if project is None:
        return

    if await _apply_frame_visual_hitl(session, project, req, decision):
        return

    action = _action_for_decision(decision)

    if req.kind is HITLKind.approve_hero:
        if action == "regen":
            project.status = ProjectStatus.generating_hero
            await session.flush()
            return
        if action == "approve":
            project.status = await _next_status_after_hero_approve(
                session, project, req
            )
            await session.flush()
        return

    # Для *_ready шагов с auto_mode воркер подхватит через maybe_auto_advance.
    # Без auto_mode — сразу двигаем проект при approve/regen/reject.
    if getattr(project, "auto_mode", False):
        return

    transition = TRANSITIONS.get(project.status)
    if transition is None or transition.kind != req.kind:
        return

    if decision is HITLDecision.approved:
        await _apply_approve(session, project, req, transition, bot=None)
    elif decision is HITLDecision.regenerate:
        await _apply_regen(
            session,
            project,
            req,
            transition,
            ReviewResult(
                decision=HITLDecision.regenerate,
                confidence=1.0,
                fix_hints=list((req.payload or {}).get("fix_hints") or []),
            ),
            bot=None,
        )
    elif decision is HITLDecision.rejected:
        await _apply_reject(
            session,
            project,
            req,
            transition,
            ReviewResult(
                decision=HITLDecision.rejected,
                confidence=1.0,
            ),
            bot=None,
        )
