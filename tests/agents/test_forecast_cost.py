"""Блокер ревью #350: прогноз не принимает отрицательные и нечисловые оценки."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.critic import PlanCritic
from app.orchestrator.contracts import PIPELINE_FORECAST_LIMIT_USD, PlanStep, forecast_cost


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
