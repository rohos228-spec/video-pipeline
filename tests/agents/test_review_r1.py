"""Неблокирующие замечания R1: сторож, revise, стрим, strong/high, sync-валидатор."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.stubs import FallbackRouter
from app.orchestrator.agents.validator_call import run_validator
from app.orchestrator.contracts import (
    ORCH_REASONING_EFFORT,
    ORCH_STEPS,
    BudgetScope,
    PlanStep,
    TaskStatus,
    ValidatorResult,
)
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


class _RecordingPlanner:
    def __init__(self) -> None:
        self.contexts: list[str] = []

    async def make_plan(self, goal: str, context: str = "", **_call: Any) -> list[PlanStep]:
        del goal
        self.contexts.append(context)
        return [PlanStep(action="noop", paid=False, mutating=False)]


class _ReviseTwice:
    def __init__(self) -> None:
        self.seen: list[str] = []

    async def review(self, plan: list[PlanStep], context: str = "", **_call: Any) -> str:
        del plan
        self.seen.append(context)
        if len(self.seen) == 1:
            return "revise: первое замечание"
        if len(self.seen) == 2:
            return "revise: второе замечание"
        return "approve"


class _SyncValidators:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.thread_names: list[str] = []

    def run(self, name: str, project_id: int, context: dict[str, Any]) -> ValidatorResult:
        self.thread_names.append(threading.current_thread().name)
        self.calls.append({"name": name, "project_id": project_id, "context": dict(context)})
        return ValidatorResult(name=name, passed=True, details="ok", blocking=False)


@pytest.mark.asyncio
async def test_revise_keeps_context_and_critic_notes(token: str) -> None:
    del token
    planner = _RecordingPlanner()
    critic = _ReviseTwice()
    rt = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=planner,
        critic=critic,
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("revise-1", "цель")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert planner.contexts[0] == ""
        assert planner.contexts[1] == "critic: revise: первое замечание"
        assert "первое замечание" in planner.contexts[2]
        assert "второе замечание" in planner.contexts[2]
        assert planner.contexts[2] != "revise"
        assert critic.seen[2] == planner.contexts[2]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_shield_records_paid_step_when_driver_is_cancelled(token: str) -> None:
    del token
    actions = MockActions()
    step = PlanStep(
        action="charge",
        params={"_wait": True, "_change_id": "chg-shield", "_cost": 2.5},
        expected="paid",
        paid=True,
        mutating=True,
    )
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([step]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("shield-1", "платный", project_id=13)
        await actions.started.wait()
        run = rt.supervisor._runs[task_id]
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert rt.tasks.get(task_id)["status"] == TaskStatus.executing.value
        actions.release.set()
        for _ in range(50):
            row = rt.tasks.list_steps(task_id)[0]
            status = rt.tasks.get(task_id)["status"]
            if row["status"] == "done" and status == TaskStatus.paused.value:
                break
            await asyncio.sleep(0.02)
        row = rt.tasks.list_steps(task_id)[0]
        assert row["status"] == "done"
        assert row["change_id"] == "chg-shield"
        assert row["cost"] == pytest.approx(2.5)
        checkpoint = rt.checkpoints.load_latest(task_id)
        assert checkpoint is not None
        assert checkpoint.position == 1
        assert checkpoint.change_ids == ["chg-shield"]

        resumed = build_runtime(
            actions=actions,
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=FixedPlanner([step]),
        )
        set_runtime(resumed)
        before = len(actions.calls)
        await resumed.supervisor.resume(task_id)
        await resumed.supervisor.wait(task_id, timeout=5)
        assert len(actions.calls) == before
        assert resumed.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_shield_records_paid_run_node_after_verify(token: str) -> None:
    del token
    actions = MockActions()
    paid_started = asyncio.Event()

    async def _on_execute(name: str, params: dict, task_ctx: dict) -> None:
        del params, task_ctx
        if name == "run_node":
            paid_started.set()

    actions.on_execute = _on_execute
    steps = [
        PlanStep(action="verify_code_state", expected="ok", paid=False, mutating=False),
        PlanStep(
            action="run_node",
            params={"node_key": "n_qc", "_wait": True, "_change_id": "chg-node", "_cost": 1.5},
            expected="ran",
            paid=True,
            mutating=True,
        ),
    ]
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(steps),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("shield-node", "нода", project_id=13)
        await asyncio.wait_for(paid_started.wait(), timeout=5)
        run = rt.supervisor._runs[task_id]
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        actions.release.set()
        for _ in range(50):
            rows = rt.tasks.list_steps(task_id)
            node = next(row for row in rows if row["action_name"] == "run_node")
            if node["status"] == "done" and node["change_id"] == "chg-node":
                break
            await asyncio.sleep(0.02)
        rows = rt.tasks.list_steps(task_id)
        node = next(row for row in rows if row["action_name"] == "run_node")
        assert node["status"] == "done"
        assert node["change_id"] == "chg-node"
        assert node["cost"] == pytest.approx(1.5)
        assert [call["name"] for call in actions.calls] == ["verify_code_state", "run_node"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_stream_uses_on_delta_and_strong_high(runtime) -> None:
    runtime.router.deltas = ["раз", " два"]
    runtime.router.answer = "не эта нарезка"
    await runtime.queue.accept("delta-1", "который час?")
    await runtime.queue.drain()
    text = "".join(
        event.payload.get("text", "")
        for event in runtime.bus.replay("delta-1", 0)
        if event.type == "token"
    )
    assert text == "раз два"
    assert runtime.router.last_call["tier"] == "strong"
    assert runtime.router.last_call["reasoning_effort"] == ORCH_REASONING_EFFORT
    assert runtime.router.last_call["budget_scope"] == BudgetScope.internal.value
    assert runtime.router.last_call["on_delta"] is not None


@pytest.mark.asyncio
async def test_fallback_classify_defaults_to_strong() -> None:
    decided = await FallbackRouter().classify("неясно")
    assert decided.tier == "strong"


@pytest.mark.asyncio
async def test_run_validator_sync_and_async() -> None:
    sync = _SyncValidators()
    async_runner = MockValidators()
    caller = threading.current_thread().name
    sync_result = await run_validator(
        sync, "plan_sanity", 3, {"phase": "before"}, run_id="orch-task-1", task_id=1
    )
    async_result = await run_validator(async_runner, "plan_sanity", 4, {}, run_id="orch-task-2", task_id=2)
    assert sync_result.passed is True
    assert async_result.passed is True
    assert sync.calls[0]["project_id"] == 3
    assert sync.thread_names[0] != caller


@pytest.mark.asyncio
async def test_sync_validator_runs_inside_supervisor(token: str) -> None:
    del token
    validators = _SyncValidators()
    rt = build_runtime(
        actions=MockActions(),
        validators=validators,
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="noop", mutating=True)]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("v-sync", "проверка", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert validators.calls
        assert validators.thread_names[0] != threading.current_thread().name
    finally:
        set_runtime(None)


def test_orch_steps_position_is_attempt_counter() -> None:
    assert "монотонный номер попытки" in ORCH_STEPS.notes
    assert "не индекс плана" in ORCH_STEPS.notes
