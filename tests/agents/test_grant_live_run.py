"""grant_approval не запускает второй драйвер, пока первый ещё жив."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.supervisor import InvalidTransition
from app.orchestrator.contracts import ApprovalEvent, LLMResponse, PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


def _budget_response() -> LLMResponse:
    return LLMResponse(
        text="бюджет кончился",
        call_id="budget-call",
        model="scripted",
        provider="52pay",
        tier="strong",
        finish_reason="stop",
        task_status=TaskStatus.waiting_approval,
        approval=ApprovalEvent(reason="budget_exhausted", payload={"spent_usd": 10}),
    )


@pytest.mark.asyncio
async def test_grant_while_driver_is_live_raises(token: str) -> None:
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="write", params={"_wait": True}, mutating=True)]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("live-grant", "пиши", project_id=13)
        await actions.started.wait()
        stopped = await runtime.supervisor.absorb_budget(
            task_id,
            "live-grant",
            _budget_response(),
            position=0,
        )
        assert stopped
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert not runtime.supervisor._runs[task_id].done()
        with pytest.raises(InvalidTransition):
            await runtime.supervisor.grant_approval("live-grant")
        actions.release.set()
        live = runtime.supervisor._runs[task_id]
        await live
        assert [call["name"] for call in actions.calls] == ["write"]
    finally:
        actions.release.set()
        set_runtime(None)
