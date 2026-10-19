"""Проблемный шаг всё равно пишет уже потраченные деньги в spend."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep
from tests.agents.mocks import MockValidators, ScriptedRouter


class _PaidRegistry:
    def metadata_of(self, name: str) -> dict[str, object]:
        return {"name": name, "paid": True, "destructive": False, "read_only": False, "risk": "low"}

    async def execute(self, name: str, params: dict, task_ctx: dict) -> dict:
        del name, params, task_ctx
        return {}


@pytest.mark.asyncio
async def test_unapproved_paid_step_still_adds_spend(token: str) -> None:
    del token
    runtime = build_runtime(
        actions=_PaidRegistry(),
        validators=MockValidators(),
        router=ScriptedRouter(),
    )
    set_runtime(runtime)
    try:
        task_id = runtime.tasks.insert("spend-problem", "платно", "executing")
        step = PlanStep(action="charge", paid=True, estimated_cost=2.5)
        step_id = runtime.tasks.insert_step(task_id, 0, step)
        problem, change_id = runtime.supervisor._write_step_done(
            task_id,
            step,
            step_id,
            {"status": "ok", "cost": 2.5, "result": {"change_id": "foreign"}},
        )
        assert problem == "unapproved_action"
        assert change_id == "foreign"
        assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == 2.5
        assert runtime.tasks.list_steps(task_id)[0]["status"] == "unapproved_action"
    finally:
        set_runtime(None)
