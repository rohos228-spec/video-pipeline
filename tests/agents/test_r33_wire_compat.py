"""Провод r3.3: старый payload валиден и grant без decision идёт прежним путём."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import (
    CONTRACT_VERSION,
    ApprovalDecisionBody,
    ApprovalEvent,
    PlanStep,
    TaskStatus,
    export_contract_schemas,
)
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


def test_r33_export_has_no_approval_decision_wire() -> None:
    schema = export_contract_schemas()
    assert schema["version"] == CONTRACT_VERSION == "ws3-r3.3.1"
    assert "ApprovalDecisionBody" not in schema["models"]
    assert "ApprovalDecision" not in schema["enums"]
    assert "approval_decision_labels" not in schema["rules"]
    note = next(item["note"] for item in schema["changelog"] if item["version"] == "ws3-r3.3")
    assert "paid_outcome_unknown" not in note
    assert "retry_grant" not in note
    body = ApprovalDecisionBody.model_validate({})
    assert body.decision is None
    assert body.retry_grant is False
    event = ApprovalEvent.model_validate({"reason": "risk", "action": "look", "task_id": 1})
    assert event.reason == "risk"
    assert event.change_id is None


@pytest.mark.asyncio
async def test_old_grant_without_decision_still_runs(token: str) -> None:
    del token
    actions = MockActions(risks={"charge": "high"})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="charge", risk="low", mutating=True, expected="pay")]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("old-grant", "списать", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert actions.calls == []
        body = ApprovalDecisionBody.model_validate({})
        await runtime.supervisor.grant_approval("old-grant", body.decision, retry_grant=body.retry_grant)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["charge"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)
