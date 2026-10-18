"""Платность шага — реестр OR план, риск платного шага не ниже medium."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.executor import effective_risk, step_is_paid, tighten_paid_step
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import ActionMetadata, PlanStep, Risk, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


class _PaidRegistry(MockActions):
    def metadata_of(self, action_name: str) -> ActionMetadata | None:
        if action_name == "charge":
            return ActionMetadata(risk="low", read_only=True, destructive=False, paid=True)
        return ActionMetadata(risk="none", read_only=True, destructive=False, paid=False)

    def risk_of(self, action_name: str) -> Risk:
        if action_name == "charge":
            return Risk.low
        return Risk.none


def test_registry_paid_overrides_a_free_plan_and_floors_risk() -> None:
    actions = _PaidRegistry()
    step = PlanStep(action="charge", paid=False, risk="none")
    assert step_is_paid(actions, step)
    assert effective_risk(actions, step) is Risk.medium
    tightened = tighten_paid_step(actions, step)
    assert tightened.paid is True
    assert tightened.risk == "medium"
    free = PlanStep(action="look", paid=False, risk="none")
    assert not step_is_paid(actions, free)
    assert tighten_paid_step(actions, free) is free


@pytest.mark.asyncio
async def test_registry_paid_false_plan_is_shielded_and_needs_approval(token: str) -> None:
    del token
    actions = _PaidRegistry()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="look", paid=False, risk="none"),
                PlanStep(
                    action="charge",
                    params={"_wait": True, "_cost": 3},
                    paid=False,
                    risk="none",
                ),
            ]
        ),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("reg-paid", "оплатить", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["look"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        approvals = [event for event in runtime.bus.replay("reg-paid", 0) if event.type == "approval_required"]
        assert approvals[-1].payload["reason"] == "risk"
        assert approvals[-1].payload["action"] == "charge"
        saved = runtime.tasks.get(task_id)["plan_json"]
        assert '"paid": true' in saved or '"paid":true' in saved.replace(" ", "")

        actions.started.clear()
        await runtime.supervisor.grant_approval("reg-paid")
        await actions.started.wait()
        assert task_id in runtime.supervisor._paid_open
        row = runtime.tasks.list_steps(task_id)[-1]
        assert row["action_name"] == "charge"
        assert row["paid"] == 1
        actions.release.set()
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["look", "charge"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert float(runtime.tasks.get(task_id)["spend_actions"]) == 3.0
        assert float(runtime.tasks.get(task_id)["spend_internal"] or 0) == 0.0
    finally:
        set_runtime(None)
