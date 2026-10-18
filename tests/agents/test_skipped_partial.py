"""Непустой skipped без partial=true ядро не считает успехом."""

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import (
    CheckpointData,
    PlanStep,
    Risk,
    TaskStatus,
    r21_result_problem,
)
from tests.agents.mocks import FixedPlanner, MockValidators, ScriptedRouter


def test_skipped_requires_partial_true() -> None:
    broken = {"status": "ok", "result": {"skipped": ["n_other"], "partial": False}}
    assert r21_result_problem(broken) == "skipped_without_partial"
    missing = {"status": "ok", "result": {"skipped": ["n_other"]}}
    assert r21_result_problem(missing) == "skipped_without_partial"
    paired = {"status": "ok", "result": {"skipped": ["n_other"], "partial": True}}
    assert r21_result_problem(paired) is None
    only_partial = {"status": "ok", "result": {"skipped": [], "partial": True}}
    assert r21_result_problem(only_partial) == "partial_without_skipped"


class _SkipWrite:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def metadata_of(self, name: str) -> dict[str, object]:
        return {
            "name": name,
            "risk": "none",
            "read_only": True,
            "destructive": False,
            "paid": False,
        }

    def risk_of(self, name: str) -> Risk:
        del name
        return Risk.none

    async def execute(self, name: str, params: dict, task_ctx: dict) -> dict:
        del params, task_ctx
        self.calls.append(name)
        return {"status": "ok", "result": {"change_id": "chg-x", "skipped": ["n"]}}


@pytest.mark.asyncio
async def test_skipped_without_partial_keeps_change_id(token: str) -> None:
    del token
    actions = _SkipWrite()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="touch", risk="none")]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("skip-id", "правка", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        rows = runtime.tasks.list_steps(task_id)
        assert rows[0]["status"] == "skipped_without_partial"
        assert rows[0]["change_id"] == "chg-x"
        assert actions.calls == ["touch"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_resume_does_not_repeat_skipped_without_partial(token: str) -> None:
    del token
    actions = _SkipWrite()
    step = PlanStep(action="touch", risk="none")
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([step]),
    )
    set_runtime(runtime)
    session = "skip-resume"
    try:
        task_id = runtime.tasks.insert(session, "правка", TaskStatus.paused.value, project_id=13)
        runtime.tasks.save_plan(task_id, [step], position=0)
        step_id = runtime.tasks.insert_step(task_id, 0, step)
        runtime.tasks.finish_step(step_id, "skipped_without_partial", "chg-x", 0.0)
        CheckpointStore().save(task_id, session, CheckpointData(plan=[step], position=0))
        await runtime.supervisor.resume(task_id)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.list_steps(task_id)[0]["change_id"] == "chg-x"
    finally:
        set_runtime(None)
