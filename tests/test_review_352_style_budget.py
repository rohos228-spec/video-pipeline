"""П.10: бюджет стиля останавливает запрос до клиента и передаёт project_id."""

from __future__ import annotations

import pytest

from app.services.style_agent.budget import BudgetExceeded, LlmBudget
from app.services.style_analyzer import _paid_ask


def _kill_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.api_tracker_hook.is_killswitch_active",
        lambda *a, **k: (False, {}),
    )
    monkeypatch.setattr(
        "app.services.style_agent.job._pipeline_text_override",
        lambda *_a, **_k: None,
    )


@pytest.mark.asyncio
async def test_style_budget_exhausted_does_not_call_client(monkeypatch) -> None:
    _kill_off(monkeypatch)

    def _boom(*_a, **_k):
        raise AssertionError("клиент не должен создаваться")

    monkeypatch.setattr("app.services.gpt_client.get_gpt_client", _boom)
    with pytest.raises(BudgetExceeded):
        await _paid_ask(
            LlmBudget(max_calls=0),
            "текст",
            [],
            system="system",
            timeout=1,
            label="style",
            project_id=13,
        )


@pytest.mark.asyncio
async def test_paid_ask_forwards_project_id(monkeypatch) -> None:
    _kill_off(monkeypatch)
    seen: dict = {}

    async def _ask(*_a, **kwargs):
        seen.update(kwargs)
        return "ok"

    monkeypatch.setattr("app.services.style_agent.llm.ask", _ask)
    out = await _paid_ask(
        LlmBudget(max_calls=1),
        "текст",
        [],
        system="system",
        timeout=1,
        label="style",
        project_id=13,
    )
    assert out == "ok"
    assert seen["project_id"] == 13
