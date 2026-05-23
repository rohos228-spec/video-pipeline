"""Побочные эффекты HITL-решений (общие для веб-API и Telegram).

Веб раньше только писал `HITLRequest.decision` в БД; без смены
`Project.status` пайплайн зависал на hero и др. Здесь зеркалим логику
из `bot.py` / `auto_advance._next_status_after_hero_approve`.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus
from app.orchestrator.auto_advance import (
    TRANSITIONS,
    _apply_approve,
    _apply_regen,
    _apply_reject,
    _next_status_after_hero_approve,
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
