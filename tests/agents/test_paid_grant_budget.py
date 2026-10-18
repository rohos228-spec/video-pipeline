"""Грант paid_outcome учитывает уже потраченную стоимость шага."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import ApprovalDecision, PlanStep, TaskStatus, server_forecast_limit
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


@pytest.mark.asyncio
async def test_paid_outcome_grant_stops_when_the_step_cost_fills_the_budget(
    token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Платный A стоит 4 при бюджете 4: следующий платный B не вызывается."""
    del token
    monkeypatch.setattr(
        "app.orchestrator.contracts.PIPELINE_FORECAST_LIMIT_USD",
        4.0,
    )
    assert server_forecast_limit() == 4.0
    paid_a = PlanStep(
        action="charge",
        params={"_wait": True, "_cost": 4, "_change_id": "chg-a"},
        paid=True,
        mutating=True,
    )
    paid_b = PlanStep(action="again", paid=True, mutating=True, params={"_cost": 1})
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([paid_a, paid_b]),
    )
    set_runtime(runtime)
    session = "paid-budget"
    try:
        task_id = await runtime.supervisor.start_task(session, "два платных", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        await runtime.supervisor.grant_approval(session)
        await actions.started.wait()
        runtime.supervisor.request_cancel(session)
        actions.release.set()
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        await runtime.supervisor.grant_approval(session, ApprovalDecision.approve)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["charge"]
        assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == 4.0
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        approvals = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
        assert approvals[-1].payload["reason"] == "budget_exhausted"
    finally:
        actions.release.set()
        set_runtime(None)
