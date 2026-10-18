"""WS1 run и WS2 metadata_of: нет модуля — заглушка, неизвестное имя платное."""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from app.orchestrator.agents.runtime import build_runtime, get_runtime, set_runtime
from app.orchestrator.agents.stubs import StubCritic, UnlinkedValidators
from app.orchestrator.agents.supervisor import InvalidTransition
from app.orchestrator.agents.wiring import ActionsBridge
from app.orchestrator.contracts import (
    ActionResult,
    PlanStep,
    TaskStatus,
    ValidatorResult,
    registry_marks_paid,
)
from tests.agents.mocks import FixedPlanner, MockValidators, ScriptedRouter


class _Registry:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    def metadata_of(self, name: str) -> dict[str, object]:
        raise KeyError(name)

    async def execute(self, name: str, params: dict, task_ctx: dict) -> ActionResult:
        del params, task_ctx
        self.calls.append(name)
        self.started.set()
        await self.release.wait()
        return ActionResult(ok=True, data={"name": name}, change_id="chg-touch", cost=0.0)


@pytest.mark.asyncio
async def test_unknown_action_is_not_in_whitelist(token: str) -> None:
    """KeyError реестра — терминальный отказ, не карточка и не execute."""
    del token
    registry = _Registry()
    actions = ActionsBridge(registry)
    assert registry_marks_paid("render_touch", actions) is True
    bare = ActionsBridge(object())
    with pytest.raises(KeyError):
        bare.metadata_of("render_touch")
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [PlanStep(action="render_touch", paid=False, mutating=False, risk="none")]
        ),
        critic=StubCritic(),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("unk-act", "коснись", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert runtime.tasks.get(task_id)["error"] == "not_in_whitelist"
        assert registry.calls == []
        assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == 0.0
        approvals = [event for event in runtime.bus.replay("unk-act", 0) if event.type == "approval_required"]
        assert approvals == []
        with pytest.raises(InvalidTransition):
            await runtime.supervisor.grant_approval("unk-act")
        assert registry.calls == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_action_change_id_is_not_approval_id() -> None:
    class _Inner:
        async def execute(self, name: str, params: dict, task_ctx: dict) -> ActionResult:
            del name, params, task_ctx
            return ActionResult(ok=True, data={}, change_id="orch-appr-4-2", cost=0.0)

        def metadata_of(self, name: str) -> dict[str, object]:
            del name
            return {"risk": "none", "read_only": True, "destructive": False, "paid": False}

    result = await ActionsBridge(_Inner()).execute("look", {}, {})
    assert result.change_id is None


def test_validators_follow_ws1_signature_or_stay_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCH_RUNTIME", raising=False)
    seen: list[tuple] = []
    module = types.ModuleType("app.orchestrator.validators")

    def run(name: str, project_id: int, context: dict | None = None):
        seen.append((name, project_id, context))
        return ValidatorResult(name=name, passed=True, details="ok", blocking=False)

    module.run = run
    monkeypatch.setitem(sys.modules, "app.orchestrator.validators", module)
    set_runtime(None)
    try:
        runtime = get_runtime()
        assert "validators" not in runtime.runtime_degraded
        runtime.supervisor.validators.run("plan_sanity", 13, {"dry_run": True})
        assert seen == [("plan_sanity", 13, {"dry_run": True})]
    finally:
        set_runtime(None)
    monkeypatch.delitem(sys.modules, "app.orchestrator.validators", raising=False)
    set_runtime(None)
    try:
        runtime = get_runtime()
        assert "validators" in runtime.runtime_degraded
        assert isinstance(runtime.supervisor.validators, UnlinkedValidators)
        assert "actions" in runtime.runtime_degraded
    finally:
        set_runtime(None)
