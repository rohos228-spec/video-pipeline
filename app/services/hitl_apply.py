"""Побочные эффекты HITL-решений (общие для веб-API и Telegram).

Веб раньше только писал `HITLRequest.decision` в БД; без смены
`Project.status` пайплайн зависал на hero и др. Здесь зеркалим логику
из `bot.py` / `auto_advance._next_status_after_hero_approve`.
"""

from __future__ import annotations

from sqlalchemy import func, select, update
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


async def claim_hitl_decision(
    session: AsyncSession,
    req: HITLRequest,
    decision: HITLDecision,
    *,
    actor: str | None = None,
    edited_prompt: str | None = None,
) -> bool:
    """Атомарно занять запрос: ``pending`` -> решение одним условным UPDATE.

    Возвращает False, если кто-то уже принял решение (второе решение не пишется). Автор решения
    лежит в ``payload["decided_by"]``, чтобы не менять схему обеих баз.
    """
    from datetime import datetime

    payload = dict(req.payload or {})
    if actor:
        payload["decided_by"] = actor[:120]
    if edited_prompt is not None and decision is HITLDecision.edit_prompt:
        payload["edited_prompt"] = edited_prompt
    result = await session.execute(
        update(HITLRequest)
        .where(HITLRequest.id == req.id, HITLRequest.decision == HITLDecision.pending)
        .values(decision=decision, decided_at=datetime.utcnow(), payload=payload)
    )
    if result.rowcount != 1:
        return False
    await session.refresh(req)
    return True


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


REJECTED_BY_HUMAN = "rejected_by_human"


def _mark_rejected(frame: Frame, value: bool) -> None:
    """N-17: кадр, отклонённый человеком, не возвращается в очередь автоматикой (requeue_failed_frames_without_png)."""
    attrs = dict(frame.attrs or {})
    if value:
        attrs[REJECTED_BY_HUMAN] = True
    elif attrs.pop(REJECTED_BY_HUMAN, None) is None:
        return
    frame.attrs = attrs


async def _charge_manual_regen(session: AsyncSession, project: Project, node: ProjectStatus) -> bool:
    """N-16: ручная перегенерация - платный перезапуск узла в общей цепочке. False: потолок, проект на паузе."""
    from app.services import restart_chain

    ok, n, cap = restart_chain.charge(project, node.value, "manual")
    if ok:
        return True
    await restart_chain.stop(session, project, node.value, n, cap, "manual")
    return False


async def request_hero_regen(session: AsyncSession, project: Project) -> bool:
    """Ручной 🔁 на hero-карточке (Telegram): платный перезапуск generating_hero идёт через общий потолок цепочки.

    True - статус переведён в generating_hero. False - потолок исчерпан, проект на паузе с причиной."""
    if not await _charge_manual_regen(session, project, ProjectStatus.generating_hero):
        return False
    project.status = ProjectStatus.generating_hero
    return True


async def _apply_frame_visual_hitl(
    session: AsyncSession,
    project: Project,
    req: HITLRequest,
    decision: HITLDecision,
    *,
    advance: bool = True,
) -> bool:
    """Решение по одному кадру — не ставим весь проект на паузу."""
    if req.frame_id is None or req.kind not in _FRAME_VISUAL_KINDS:
        return False
    frame = await session.get(Frame, req.frame_id)
    if frame is None:
        return True
    if decision in (HITLDecision.regenerate, HITLDecision.edit_prompt):
        node = (ProjectStatus.generating_images if req.kind is HITLKind.approve_images
                else ProjectStatus.generating_videos)
        if not await _charge_manual_regen(session, project, node):
            return True  # потолок: кадр не возвращается в очередь, проект на паузе с причиной
    _mark_rejected(frame, decision is HITLDecision.rejected)
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
    if advance and not getattr(project, "auto_mode", False):
        await _maybe_advance_visual_ready(session, project, req.kind)
    return True


async def apply_hitl_side_effects(
    session: AsyncSession,
    req: HITLRequest,
    decision: HITLDecision,
    *,
    advance: bool = True,
) -> None:
    """Обновить статус проекта после решения по HITL (если нужно).

    ``advance=False`` (слой действий, C-14): решение записывается, а следующий платный шаг не запускается; его
    запускают отдельным действием с оценкой цены и потолком."""
    if decision is HITLDecision.pending:
        return

    project = await session.get(Project, req.project_id)
    if project is None:
        return

    if await _apply_frame_visual_hitl(session, project, req, decision, advance=advance):
        return

    action = _action_for_decision(decision)

    if req.kind is HITLKind.approve_hero:
        if action == "regen":
            if not await _charge_manual_regen(session, project, ProjectStatus.generating_hero):
                return
            project.status = ProjectStatus.generating_hero
            await session.flush()
            return
        if action == "approve":
            if not advance:
                return
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
        if advance:
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
            manual=True,
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
