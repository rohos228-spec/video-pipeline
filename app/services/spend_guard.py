"""Активный бюджет платного вызова. Нет бюджета — вызов не ограничен здесь."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from app.services.style_agent.budget import LlmBudget

_active: ContextVar[LlmBudget | None] = ContextVar("spend_budget", default=None)
_pipeline: LlmBudget | None = None
# Прод без явного лимита не блокируем: бюджет нужен, чтобы записать трату.
_PIPELINE_MAX_CALLS = 10**9


def pipeline_spend_budget() -> LlmBudget:
    """Общий бюджет прод-пути, если вызывающий не передал свой."""
    global _pipeline
    if _pipeline is None:
        _pipeline = LlmBudget(max_calls=_PIPELINE_MAX_CALLS)
    return _pipeline


def active_spend_budget() -> LlmBudget | None:
    return _active.get()


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


def note_paid_spend(label: str = "") -> None:
    """Пишет трату после успешного платного вызова, не до него."""
    budget = _active.get()
    if budget is None:
        return
    budget.note_spent(label)
