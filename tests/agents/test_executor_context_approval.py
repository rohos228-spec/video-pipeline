"""После одобрения исполнитель и plan-sanity получают context['approval']."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.supervisor import InvalidTransition
from app.orchestrator.contracts import ActionResult, ApprovalDecision, PlanStep, Risk, TaskStatus
from tests.agents.mocks import FixedPlanner, MockValidators, ScriptedCritic, ScriptedRouter


class _Registry:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.table = {
            "look": {"risk": "none", "read_only": True, "destructive": False, "paid": False},
            "note": {"risk": "medium", "read_only": True, "destructive": False, "paid": False},
        }

    def metadata_registry(self) -> dict[str, dict[str, object]]:
        return {name: self.metadata_of(name) for name in self.table}

    def metadata_of(self, name: str) -> dict[str, object]:
        if name not in self.table:
            raise KeyError(name)
        return {"name": name, **self.table[name]}

    def risk_of(self, name: str) -> Risk:
        return Risk(str(self.metadata_of(name)["risk"]))

    async def execute(self, name: str, params: dict, task_ctx: dict) -> ActionResult:
        del params
        self.calls.append({"name": name, "ctx": dict(task_ctx)})
        return ActionResult(ok=True, data={"name": name}, change_id=None, cost=0.0)


def _runtime(actions: _Registry, validators: MockValidators, steps: list[PlanStep]):
    return build_runtime(
        actions=actions,
        validators=validators,
        router=ScriptedRouter(),
        planner=FixedPlanner(steps),
        critic=ScriptedCritic(["approve"]),
    )


@pytest.mark.asyncio
async def test_executor_context_approval_shape() -> None:
    actions = _Registry()
    validators = MockValidators()
    runtime = _runtime(
        actions,
        validators,
        [
            PlanStep(action="look", risk="none"),
            PlanStep(action="note", risk="none", mutating=True),
        ],
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("appr-shape", "заметка", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["look"]
        assert "approval" not in actions.calls[0]["ctx"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        opened = [
            event for event in runtime.bus.replay("appr-shape", 0) if event.type == "approval_required"
        ]
        change_id = str(opened[-1].payload["change_id"])
        await runtime.supervisor.grant_approval("appr-shape")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["look", "note"]
        approval = actions.calls[1]["ctx"]["approval"]
        assert approval == {
            "change_id": change_id,
            "action": "note",
            "risk": "medium",
            "destructive": False,
            "granted": True,
            "cc_change_id": None,
        }
        assert change_id.startswith(f"orch-appr-{task_id}-")
        dry = [call for call in validators.calls if call["context"].get("dry_run") is True]
        assert dry
        assert all("approval" not in call["context"] for call in dry)
        sanity = [
            call
            for call in validators.calls
            if call["name"] == "plan_sanity" and call["context"].get("action") == "note"
        ]
        assert sanity
        assert all(call["context"]["approval"] == approval for call in sanity)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_denied_grant_does_not_call_the_executor() -> None:
    actions = _Registry()
    runtime = _runtime(
        actions,
        MockValidators(),
        [PlanStep(action="note", risk="none", mutating=True)],
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("appr-deny", "отказ", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        with pytest.raises(InvalidTransition):
            await runtime.supervisor.grant_approval("appr-deny", ApprovalDecision.deny)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)
