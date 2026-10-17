"""WS1 validators.run: sync и async, бюджетный blocking → waiting_approval."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.validator_call import run_validator
from app.orchestrator.contracts import (
    VALIDATOR_CONTEXT_DRY_RUN,
    ApprovalReason,
    PlanStep,
    TaskStatus,
    ValidatorResult,
)
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


class SyncValidators:
    def __init__(self, script: dict[tuple[str, str], ValidatorResult] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.script = script or {}
        self.thread_names: list[str] = []

    def run(self, name: str, project_id: int, context: dict[str, Any]) -> ValidatorResult:
        self.thread_names.append(threading.current_thread().name)
        self.calls.append({"name": name, "project_id": project_id, "context": dict(context)})
        phase = "dry_run" if context.get(VALIDATOR_CONTEXT_DRY_RUN) is True else str(context.get("phase") or "")
        scripted = self.script.get((name, phase))
        if scripted is not None:
            return scripted
        return ValidatorResult(name=name, passed=True, details="ok", blocking=False)


def _budget(name: str) -> ValidatorResult:
    return ValidatorResult(
        name=name,
        passed=False,
        blocking=True,
        details={"reason": "budget_exhausted", "spent_usd": 10},
    )


@pytest.mark.asyncio
async def test_run_validator_accepts_sync_and_async() -> None:
    sync = SyncValidators()
    async_runner = MockValidators()
    caller = threading.current_thread().name

    sync_result = await run_validator(
        sync, "plan_sanity", 3, {"dry_run": True}, run_id="orch-task-1", task_id=1
    )
    async_result = await run_validator(
        async_runner, "plan_sanity", 3, {"dry_run": True}, run_id="orch-task-1", task_id=1
    )

    assert sync_result.passed is True
    assert async_result.passed is True
    assert sync.calls[0]["name"] == "plan_sanity"
    assert async_runner.calls[0]["name"] == "plan_sanity"
    assert sync.thread_names[0] != caller


@pytest.mark.asyncio
async def test_sync_validator_dry_run_reaches_done(token: str) -> None:
    del token
    validators = SyncValidators()
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=validators,
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="noop", mutating=True, expected="ok")]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("v-sync", "проверка", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        dry = [call for call in validators.calls if call["context"].get(VALIDATOR_CONTEXT_DRY_RUN) is True]
        assert [call["name"] for call in dry] == ["plan_sanity", "no_deletions", "spend_guard"]
        assert {call["project_id"] for call in dry} == {13}
        assert validators.thread_names
        assert all(name != threading.current_thread().name for name in validators.thread_names)
        assert [call["name"] for call in actions.calls] == ["noop"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_blocking_budget_waits_instead_of_failing(token: str) -> None:
    del token
    validators = SyncValidators({("spend_guard", "dry_run"): _budget("spend_guard")})
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=validators,
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="noop", mutating=True, expected="ok")]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("v-budget", "план", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert TaskStatus.failed.value not in [status for _, status in rt.supervisor.history]
        assert actions.calls == []
        approvals = [event for event in rt.bus.replay("v-budget", 0) if event.type == "approval_required"]
        assert len(approvals) == 1
        assert approvals[0].payload["reason"] == ApprovalReason.budget_exhausted.value
        assert approvals[0].payload["payload"]["reason"] == "budget_exhausted"
        checkpoint = rt.checkpoints.load_latest(task_id)
        assert checkpoint is not None
        assert checkpoint.position == 0
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_spend_guard_context_has_run_id_and_missing_run_fails(token: str) -> None:
    del token
    missing = ValidatorResult(
        name="spend_guard",
        passed=False,
        blocking=True,
        details={"reason": "missing_run_id"},
    )
    validators = SyncValidators({("spend_guard", "after"): missing})
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=validators,
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="noop", mutating=True, expected="ok")]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("v-run", "план", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert TaskStatus.waiting_approval.value not in [status for _, status in rt.supervisor.history]
        reasons = [
            event.payload.get("reason")
            for event in rt.bus.replay("v-run", 0)
            if event.type == "approval_required"
        ]
        assert reasons == []
        assert validators.calls
        for call in validators.calls:
            assert isinstance(call["context"].get("run_id"), str)
            assert call["context"]["run_id"].strip()
            assert call["context"].get("task_id") not in (None, "")
        live = [
            call
            for call in validators.calls
            if call["name"] == "spend_guard" and call["context"].get("phase") == "after"
        ]
        assert live
        assert live[0]["context"]["run_id"] == f"orch-task-{task_id}"
        assert live[0]["context"]["task_id"] == task_id
        with pytest.raises(ValueError, match="run_id"):
            await run_validator(validators, "spend_guard", 13, {"phase": "after"}, task_id=task_id)
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_other_blocking_validator_still_fails_the_task(token: str) -> None:
    del token
    blocked = ValidatorResult(name="plan_sanity", passed=False, blocking=True, details={"reason": "nope"})
    validators = SyncValidators({("plan_sanity", "before"): blocked})
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=validators,
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="noop", mutating=True, expected="ok")]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("v-fail", "план", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert actions.calls == []
        reasons = [
            event.payload.get("reason")
            for event in rt.bus.replay("v-fail", 0)
            if event.type == "approval_required"
        ]
        assert ApprovalReason.budget_exhausted.value not in reasons
    finally:
        set_runtime(None)
