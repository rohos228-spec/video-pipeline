"""create_node: новая группа повышает риск из RISK_ESCALATIONS, не из имени."""

from __future__ import annotations

import sys
import types
from collections import namedtuple

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import (
    ActionResult,
    PlanStep,
    Risk,
    TaskStatus,
    escalation_for,
    load_risk_escalations,
    not_in_whitelist,
)
from tests.agents.mocks import FixedPlanner, MockValidators, ScriptedCritic, ScriptedRouter


class _Registry:
    def __init__(self, *, risk: str) -> None:
        self.calls: list[dict] = []
        self.risk = risk

    def metadata_of(self, name: str) -> dict[str, object]:
        if name != "create_node":
            raise KeyError(name)
        return {
            "name": name,
            "risk": self.risk,
            "read_only": True,
            "destructive": False,
            "paid": False,
        }

    def risk_of(self, name: str) -> Risk:
        return Risk(str(self.metadata_of(name)["risk"]))

    async def execute(self, name: str, params: dict, task_ctx: dict) -> ActionResult:
        del params
        self.calls.append({"name": name, "ctx": dict(task_ctx)})
        return ActionResult(ok=True, data={"name": name}, change_id=None, cost=0.0)


def test_fallback_escalates_new_group_and_at_sign_is_not_a_name() -> None:
    rules = load_risk_escalations()
    matched = [rule for rule in rules if getattr(rule, "when", None) == "group_is_new"]
    assert matched
    assert matched[0].action == "create_node"
    assert matched[0].risk == "high"
    assert matched[0].destructive is True
    assert escalation_for("create_node", {"group_is_new": True}) is matched[0]
    assert escalation_for("create_node", {"group_is_new": False}) is None
    assert escalation_for("create_node", {}) is None
    assert escalation_for("create_node@new_group", {"group_is_new": True}) is None
    assert not_in_whitelist("create_node@new_group", _Registry(risk="low")) is True
    assert not_in_whitelist("create_node", _Registry(risk="low")) is False


def test_imported_risk_escalations_win_over_the_fallback() -> None:
    rule = namedtuple("RiskEscalation", "action when risk read_only destructive paid")
    module = types.ModuleType("app.orchestrator.action_contracts.contracts")
    module.RISK_ESCALATIONS = (rule("create_node", "group_is_new", "high", False, True, False),)
    package = types.ModuleType("app.orchestrator.action_contracts")
    package.contracts = module
    saved = {
        name: sys.modules.get(name)
        for name in (
            "app.orchestrator.action_contracts",
            "app.orchestrator.action_contracts.contracts",
        )
    }
    sys.modules["app.orchestrator.action_contracts"] = package
    sys.modules["app.orchestrator.action_contracts.contracts"] = module
    try:
        loaded = load_risk_escalations()
        assert loaded[0].action == "create_node"
        assert loaded[0].destructive is True
        assert "create_node@new_group" not in {getattr(item, "action", "") for item in loaded}
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


async def _run(params: dict, *, risk: str = "low") -> tuple[object, _Registry, int]:
    actions = _Registry(risk=risk)
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [PlanStep(action="create_node", params=params, mutating=True, risk="none")]
        ),
        critic=ScriptedCritic(["approve"]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("cn-esc", "создай ноду", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        return runtime, actions, task_id
    finally:
        set_runtime(None)


def _statuses(runtime, task_id: int) -> list[str]:
    return [status for tid, status in runtime.supervisor.history if tid == task_id]


@pytest.mark.asyncio
async def test_create_node_new_group_needs_approval_then_runs() -> None:
    actions = _Registry(risk="low")
    validators = MockValidators()
    runtime = build_runtime(
        actions=actions,
        validators=validators,
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(
                    action="create_node",
                    params={"group_is_new": True},
                    mutating=True,
                    risk="none",
                )
            ]
        ),
        critic=ScriptedCritic(["approve"]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("cn-new", "новая группа", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        opened = [event for event in runtime.bus.replay("cn-new", 0) if event.type == "approval_required"]
        assert opened[-1].payload["action"] == "create_node"
        assert opened[-1].payload["risk"] == "high"
        await runtime.supervisor.grant_approval("cn-new")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["create_node"]
        approval = actions.calls[0]["ctx"]["approval"]
        assert approval["action"] == "create_node"
        assert approval["risk"] == "high"
        assert approval["destructive"] is True
        assert approval["granted"] is True
        assert approval["change_id"].startswith(f"orch-appr-{task_id}-")
        assert approval["cc_change_id"] is None
        sanity = [
            call
            for call in validators.calls
            if call["name"] == "plan_sanity" and call["context"].get("phase") == "before"
        ]
        assert sanity
        assert sanity[-1]["context"]["approval"] == approval
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_create_node_existing_group_follows_base_risk() -> None:
    runtime, actions, task_id = await _run({"group_is_new": False})
    assert [call["name"] for call in actions.calls] == ["create_node"]
    assert "approval" not in actions.calls[0]["ctx"]
    assert "waiting_approval" not in _statuses(runtime, task_id)
    assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value

    held = _Registry(risk="medium")
    medium = build_runtime(
        actions=held,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [PlanStep(action="create_node", params={}, mutating=True, risk="none")]
        ),
        critic=ScriptedCritic(["approve"]),
    )
    set_runtime(medium)
    try:
        task_id = await medium.supervisor.start_task("cn-base", "есть группа", project_id=13)
        await medium.supervisor.wait(task_id, timeout=5)
        assert held.calls == []
        assert medium.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        opened = [event for event in medium.bus.replay("cn-base", 0) if event.type == "approval_required"]
        assert opened[-1].payload["risk"] == "medium"
        assert opened[-1].payload["action"] == "create_node"
    finally:
        set_runtime(None)
