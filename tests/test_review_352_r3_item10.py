"""Ревью #352 п.10: kill switch и бюджет не глотаются и не пишутся как fallback."""

import pytest

from app.services.api_tracker_hook import ApiEmergencyBlockedError, reraise_if_spend_blocked
from app.services.style_agent.budget import BudgetExceeded


def test_kill_switch_and_budget_propagate() -> None:
    with pytest.raises(ApiEmergencyBlockedError):
        reraise_if_spend_blocked(ApiEmergencyBlockedError("стоп"))
    with pytest.raises(BudgetExceeded):
        reraise_if_spend_blocked(BudgetExceeded("лимит"))
    reraise_if_spend_blocked(TimeoutError("сеть"))
