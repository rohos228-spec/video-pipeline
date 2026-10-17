"""Блокер ревью #350: прогноз не принимает плохие оценки как сумму."""

from __future__ import annotations

import json

import pytest

from app.orchestrator.agents.critic import PlanCritic
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import (
    PIPELINE_FORECAST_LIMIT_USD,
    PlanStep,
    TaskStatus,
    UnknownForecast,
    cost_is_unknown,
    forecast_cost,
    nonneg_cost,
)
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter

_UNKNOWN_COSTS = [
    pytest.param(float("inf"), id="inf"),
    pytest.param(float("-inf"), id="neg-inf"),
    pytest.param(float("nan"), id="nan"),
    pytest.param(True, id="bool-true"),
    pytest.param(False, id="bool-false"),
    pytest.param("0", id="numeric-string-zero"),
    pytest.param("80", id="numeric-string"),
]


def _step(raw: object, slot: str) -> PlanStep:
    if slot == "field":
        return PlanStep(action="look", estimated_cost=raw)
    return PlanStep(action="look", params={"estimated_cost": raw})


def test_negative_and_non_numeric_estimates_do_not_lower_the_forecast() -> None:
    clamped = PlanStep(action="charge", estimated_cost=-100)
    assert clamped.estimated_cost == 0
    noisy = PlanStep(action="charge", params={"estimated_cost": "abc"})
    assert forecast_cost([noisy]) == 0
    negative_param = PlanStep(action="charge", params={"estimated_cost": -100})
    assert forecast_cost([negative_param]) == 0
    honest = PlanStep(action="charge", estimated_cost=80)
    total = forecast_cost([clamped, honest])
    assert total == 80
    assert total > PIPELINE_FORECAST_LIMIT_USD


@pytest.mark.asyncio
async def test_negative_estimate_cannot_sneak_past_the_forecast_limit() -> None:
    critic = PlanCritic(router=None, rag=None)
    plan = [
        PlanStep(action="noop", estimated_cost=-100),
        PlanStep(action="noop", estimated_cost=80),
    ]
    assert forecast_cost(plan) == 80
    assert await critic.review(plan) == "revise"


@pytest.mark.parametrize("slot", ["field", "params"])
@pytest.mark.parametrize("raw", _UNKNOWN_COSTS)
@pytest.mark.asyncio
async def test_unknown_cost_blocks_instead_of_zero(
    raw: object,
    slot: str,
    token: str,
    request: pytest.FixtureRequest,
) -> None:
    del token
    with pytest.raises(UnknownForecast):
        nonneg_cost(raw)
    step = _step(raw, slot)
    assert cost_is_unknown(step.params["estimated_cost"])
    with pytest.raises(UnknownForecast):
        forecast_cost([step])
    restored = PlanStep.model_validate(json.loads(json.dumps(step.model_dump())))
    assert cost_is_unknown(restored.params["estimated_cost"])
    with pytest.raises(UnknownForecast):
        forecast_cost([restored])

    router = ScriptedRouter()
    assert await PlanCritic(router=router, rag=None).review([step]) == "reject"
    assert router.call_count == 0

    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([step]),
    )
    set_runtime(runtime)
    session = request.node.name
    try:
        task_id = await runtime.supervisor.start_task(session, "прогноз")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        approvals = [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]
        assert approvals[-1].payload["reason"] == "unknown_cost"
        await runtime.supervisor.grant_approval(session)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)
