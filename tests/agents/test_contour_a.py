"""Контур A: план из пяти шагов, одно approval, удаление кадра не исполняется."""

from __future__ import annotations

import json

import pytest

from app.orchestrator.agents.critic import RouterCritic
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus, params_request_deletion, step_is_deletion
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter

_INACTIVE = {"shot": {"id": 1, "активен": False}}


def _steps() -> list[dict]:
    names = ["look", "mark_shot", "list", "note", "save"]
    steps = []
    for name in names:
        params = dict(_INACTIVE) if name == "mark_shot" else {}
        steps.append(
            {
                "action": name,
                "params": params,
                "expected": name,
                "paid": False,
                "mutating": name == "note",
                "risk": "high" if name == "note" else "none",
                "estimated_cost": 0,
            }
        )
    return steps


@pytest.mark.asyncio
async def test_contour_a_one_approval_and_inactive_shot_is_not_deletion(token: str) -> None:
    del token
    inactive = PlanStep(action="mark_shot", params=dict(_INACTIVE))
    actions = MockActions()
    assert params_request_deletion(inactive.params) is False
    assert step_is_deletion(inactive, actions) is False
    router = ScriptedRouter()
    router.script = [
        json.dumps({"steps": _steps()}),
        "approve",
        json.dumps({"steps": [{"action": "delete_child_frame", "params": {}, "expected": "gone"}]}),
    ]
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router,
        planner=RouterPlanner(router),
        critic=RouterCritic(router),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("contour-a", "сделай обход", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert [call["name"] for call in actions.calls] == ["look", "mark_shot", "list"]
        assert actions.calls[1]["params"]["shot"]["активен"] is False
        approvals = [event for event in runtime.bus.replay("contour-a", 0) if event.type == "approval_required"]
        assert len(approvals) == 1
        assert approvals[0].payload["reason"] == "risk"
        await runtime.supervisor.grant_approval("contour-a")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert [call["name"] for call in actions.calls] == ["look", "mark_shot", "list", "note", "save"]
        assert len([event for event in runtime.bus.replay("contour-a", 0) if event.type == "approval_required"]) == 1

        blocked = await runtime.supervisor.start_task("contour-del", "сними кадр", project_id=13)
        await runtime.supervisor.wait(blocked, timeout=5)
        assert runtime.tasks.get(blocked)["status"] == TaskStatus.failed.value
        assert runtime.tasks.get(blocked)["error"] == "deletion_blocked"
        assert "delete_child_frame" not in [call["name"] for call in actions.calls]
    finally:
        set_runtime(None)
