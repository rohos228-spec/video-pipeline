"""Исполнение шагов только через actions.execute.

Независимые read-only шаги одной parallel_group собирает супервизор
и отдаёт сюда пачкой. Мутации и платные шаги сюда приходят по одному.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.orchestrator.contracts import ActionResult, PlanStep, Risk, max_risk


def effective_risk(actions: Any, step: PlanStep) -> Risk:
    """Approval-риск шага: max(реестр действий, подсказка плана).

    Нет ``risk_of`` — действие неизвестно, риск ``high``. План не понижает реестр.
    """
    risk_of = getattr(actions, "risk_of", None)
    registry = Risk.high if risk_of is None else risk_of(step.action)
    return max_risk(step.risk, registry)


async def run_many(executor: Any, steps: list[PlanStep], ctx: dict[str, Any]) -> list[ActionResult]:
    """Параллельный прогон read-only шагов. Один шаг — тот же путь."""

    async def _one(step: PlanStep) -> ActionResult:
        result = await executor.run(step, ctx)
        if isinstance(result, ActionResult):
            return result
        return ActionResult.model_validate(result)

    if len(steps) == 1:
        return [await _one(steps[0])]
    return list(await asyncio.gather(*(_one(step) for step in steps)))
