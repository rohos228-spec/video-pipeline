"""Исполнение шагов только через actions.execute.

Независимые read-only шаги одной parallel_group собирает супервизор
и отдаёт сюда пачкой. Мутации и платные шаги сюда приходят по одному.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.orchestrator.contracts import ActionResult, PlanStep, Risk, max_risk


def step_is_paid(actions: Any, step: PlanStep) -> bool:
    """Платность: реестр OR план. План может только ужесточить, не снять."""
    if step.paid:
        return True
    metadata_of = getattr(actions, "metadata_of", None)
    if metadata_of is None:
        return False
    try:
        meta = metadata_of(step.action)
    except (TypeError, ValueError, KeyError):
        return False
    if meta is None:
        return False
    if isinstance(meta, dict):
        return meta.get("paid") is True
    return getattr(meta, "paid", False) is True


def effective_risk(actions: Any, step: PlanStep) -> Risk:
    """Approval-риск шага: max(реестр действий, подсказка плана).

    Нет ``risk_of`` — действие неизвестно, риск ``high``. План не понижает реестр.
    Платный шаг не опускается ниже ``medium``: none/low не обходят approval.
    """
    risk_of = getattr(actions, "risk_of", None)
    registry = Risk.high if risk_of is None else risk_of(step.action)
    risk = max_risk(step.risk, registry)
    if step_is_paid(actions, step):
        return max_risk(risk, Risk.medium)
    return risk


def tighten_paid_step(actions: Any, step: PlanStep) -> PlanStep:
    """Записать в шаг платность реестра и пол риска до ``medium``."""
    if not step_is_paid(actions, step):
        return step
    risk = max_risk(step.risk, Risk.medium).value
    if step.paid and step.risk == risk:
        return step
    return step.model_copy(update={"paid": True, "risk": risk})


async def run_many(
    executor: Any,
    steps: list[PlanStep],
    ctx: dict[str, Any],
) -> list[ActionResult | BaseException]:
    """Параллельный прогон read-only шагов. Один шаг — тот же путь."""

    async def _one(step: PlanStep) -> ActionResult:
        result = await executor.run(step, ctx)
        if isinstance(result, ActionResult):
            return result
        return ActionResult.model_validate(result)

    if len(steps) == 1:
        return [await _one(steps[0])]
    # Исключение шага остаётся его результатом: сосед по группе не помечается
    # проваленным, если сам завершился. Сдвигов и семафора у группы нет.
    return list(await asyncio.gather(*(_one(step) for step in steps), return_exceptions=True))
