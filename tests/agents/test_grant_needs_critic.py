"""Блокер ревью #350: грант не исполняет план, который критик не одобрил."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import (
    ApprovalEvent,
    CheckpointData,
    LLMResponse,
    PlanStep,
    TaskStatus,
)
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


def _budget() -> LLMResponse:
    return LLMResponse(
        text="стоп",
        task_status=TaskStatus.waiting_approval,
        approval=ApprovalEvent(reason="budget_exhausted"),
    )


class _FlipPlanner:
    def __init__(self) -> None:
        self.calls = 0

    async def make_plan(self, goal: str, context: str = "", **_call: object) -> list[PlanStep]:
        del goal, context
        self.calls += 1
        if self.calls == 1:
            self.last_response = _budget()
            return [PlanStep(action="unapproved", mutating=True)]
        self.last_response = None
        return [PlanStep(action="approved", mutating=True)]


@pytest.mark.asyncio
async def test_grant_replans_instead_of_executing_unapproved_plan(token: str) -> None:
    del token
    actions = MockActions()
    planner = _FlipPlanner()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=planner,
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("grant-replan", "цель", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        await runtime.supervisor.grant_approval("grant-replan")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["approved"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert planner.calls >= 2
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_budget_checkpoint_does_not_reuse_old_position(token: str) -> None:
    del token
    runtime = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="look")]),
    )
    set_runtime(runtime)
    try:
        task_id = runtime.tasks.insert("old-pos", "цель", TaskStatus.planning.value)
        old = [PlanStep(action="a"), PlanStep(action="b"), PlanStep(action="c")]
        runtime.checkpoints.save(
            task_id,
            "old-pos",
            CheckpointData(plan=old, position=2),
        )
        runtime.tasks.save_plan(task_id, [PlanStep(action="only")])
        await runtime.supervisor._absorb_budget(task_id, "old-pos", _budget())
        saved = runtime.checkpoints.load_latest(task_id)
        assert saved is not None
        assert saved.position == 0
        assert [step.action for step in saved.plan] == ["only"]
    finally:
        set_runtime(None)
