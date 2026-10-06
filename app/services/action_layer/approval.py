"""Серверное подтверждение платных/разрушающих планов.

Подтверждение создаёт только оператор отдельным аутентифицированным вызовом
``POST /plans/{id}/approve``. Оно привязано к хэшу плана, живёт ограниченное
время и расходуется атомарно при запуске. Планировщик/модель не получает ни
способа его создать, ни способа передать: в payload плана нет такого поля, а
``run`` не принимает идентификатор подтверждения от вызывающего.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.services.action_layer import store

_FORBIDDEN_PREFIXES = ("planner:", "model:", "agent:", "system")


class ApprovalError(Exception):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class OperatorIdentity:
    """Личность оператора из AuthDep (тот же формат, что в observations)."""
    id: str

    @classmethod
    def from_token(cls, token: str | None) -> "OperatorIdentity":
        if not token:
            return cls("local_web_operator")
        return cls("web_operator:" + hashlib.sha256(token.encode()).hexdigest()[:16])


def _check_operator(operator: OperatorIdentity) -> None:
    if not isinstance(operator, OperatorIdentity) or not operator.id.strip():
        raise ApprovalError("operator_required", "Для подтверждения нужна личность оператора.")
    if operator.id.lower().startswith(_FORBIDDEN_PREFIXES):
        raise ApprovalError("operator_required", "Подтвердить может только оператор-человек.")


def default_ttl_s() -> int:
    from app.settings import settings
    return int(settings.action_layer_approval_ttl_s)


async def create_approval(session, plan: "store.ActionPlan", operator: OperatorIdentity, *,
                          ttl_s: int | None = None, now: datetime | None = None
                          ) -> "store.ActionApproval":
    _check_operator(operator)
    now = now or datetime.utcnow()
    if plan.status not in (store.PLAN_PROPOSED, store.PLAN_STOPPED):
        raise ApprovalError("plan_not_approvable", f"План сейчас в состоянии «{plan.status}»: подтвердить его нельзя.")
    if not plan.needs_approval:
        raise ApprovalError("approval_not_needed", "В плане только чтение и безопасные локальные шаги: подтверждение не нужно.")
    # Новое подтверждение отменяет прежние неиспользованные.
    await session.execute(update(store.ActionApproval).where(
        store.ActionApproval.plan_id == plan.id,
        store.ActionApproval.used_at.is_(None),
        store.ActionApproval.expires_at > now).values(expires_at=now))
    row = store.ActionApproval(
        plan_id=plan.id, plan_hash=plan.plan_hash, operator=operator.id, created_at=now,
        expires_at=now + timedelta(seconds=ttl_s if ttl_s is not None else default_ttl_s()))
    session.add(row)
    store.append_history(session, event="approval_created", plan_id=plan.id,
                         actor=operator.id, request_text=plan.request_text,
                         outcome="approved")
    return row


async def consume_approval(session, plan: "store.ActionPlan", *,
                           now: datetime | None = None) -> "store.ActionApproval":
    """Атомарно расходует действующее подтверждение этого плана и его хэша."""
    now = now or datetime.utcnow()
    candidate = await session.scalar(select(store.ActionApproval).where(
        store.ActionApproval.plan_id == plan.id,
        store.ActionApproval.plan_hash == plan.plan_hash,
        store.ActionApproval.used_at.is_(None),
        store.ActionApproval.expires_at > now,
    ).order_by(store.ActionApproval.created_at.desc()))
    if candidate is None:
        raise ApprovalError("approval_required",
                            "Нет действующего подтверждения этого плана (не выдано, истекло или уже использовано).")
    result = await session.execute(update(store.ActionApproval).where(
        store.ActionApproval.id == candidate.id,
        store.ActionApproval.used_at.is_(None),
        store.ActionApproval.expires_at > now,
        store.ActionApproval.plan_hash == plan.plan_hash).values(used_at=now))
    if result.rowcount != 1:
        raise ApprovalError("approval_required", "Подтверждение только что использовано другим запуском.")
    candidate.used_at = now
    store.append_history(session, event="approval_consumed", plan_id=plan.id,
                         actor=candidate.operator, outcome="consumed")
    return candidate
