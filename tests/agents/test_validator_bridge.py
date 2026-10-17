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

    sync_result = await run_validator(sync, "plan_sanity", 3, {"dry_run": True})
    async_result = await run_validator(async_runner, "plan_sanity", 3, {"dry_run": True})

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
        task_id = await rt.supervisor.start_task("v-sync", "проверка")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        dry = [call for call in validators.calls if call["context"].get(VALIDATOR_CONTEXT_DRY_RUN) is True]
        assert [call["name"] for call in dry] == ["plan_sanity", "no_deletions", "spend_guard"]
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
        task_id = await rt.supervisor.start_task("v-budget", "план")
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
        task_id = await rt.supervisor.start_task("v-fail", "план")
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
