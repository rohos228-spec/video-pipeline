"""Общий вызов ``router.call`` для planner и critic.

Собственные ходы оркестратора всегда передают ``budget_scope=internal``
и непустой ``run_id``. Пустой id не уходит в роутер: ``spent(None)``
не бакет и на учёт бюджета не влияет.
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.contracts import BudgetScope, require_internal_run_id


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
    reasoning_effort: str | None = None,
    on_delta: Any = None,
) -> Any:
    scope = budget_scope or BudgetScope.internal.value
    bound = None if run_id is None else str(run_id)
    require_internal_run_id(bound, scope)
    return await router.call(
        tier,
        messages,
        stream,
        run_id=bound,
        task_id=None if task_id is None else str(task_id),
        agent_role=agent_role,
        budget_scope=scope,
        reasoning_effort=reasoning_effort,
        on_delta=on_delta,
    )
