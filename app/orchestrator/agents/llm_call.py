"""Общий вызов ``router.call`` для супервизора, planner, critic и executor.

Собственные ходы оркестратора всегда передают ``budget_scope=internal``.
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.contracts import BudgetScope


async def router_call(
    router: Any,
    tier: str,
    messages: list[dict[str, Any]],
    *,
    stream: bool = False,
    run_id: str | None = None,
    task_id: int | str | None = None,
    agent_role: str | None = None,
    budget_scope: str | None = None,
) -> Any:
    return await router.call(
        tier,
        messages,
        stream,
        run_id=run_id,
        task_id=None if task_id is None else str(task_id),
        agent_role=agent_role,
        budget_scope=budget_scope or BudgetScope.internal.value,
    )
