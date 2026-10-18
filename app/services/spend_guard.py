"""Активный бюджет платного вызова. Нет бюджета — вызов не ограничен здесь."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from app.services.style_agent.budget import LlmBudget

_active: ContextVar[LlmBudget | None] = ContextVar("spend_budget", default=None)


@contextmanager
def use_spend_budget(budget: LlmBudget) -> Iterator[LlmBudget]:
    token = _active.set(budget)
    try:
        yield budget
    finally:
        _active.reset(token)


def guard_paid_spend(label: str) -> None:
    """Если бюджет включён и исчерпан — BudgetExceeded до сетевого вызова."""
    budget = _active.get()
    if budget is None:
        return
    budget.reserve(label)
