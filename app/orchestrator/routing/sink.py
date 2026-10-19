"""Сток строк ``orch_llm_calls``. Подменяется в тестах и WS3.

Продакшен-реализация пишет в БД (миграцию делает WS3). Здесь только интерфейс
и память. Текст промптов и ответов в запись не кладётся.
"""

from __future__ import annotations

from typing import Protocol

from app.orchestrator.routing.types import LlmCallRecord


class LlmCallSink(Protocol):
    def record(self, row: LlmCallRecord) -> None:
        """Одна попытка — одна строка."""


class InMemoryLlmCallSink:
    def __init__(self) -> None:
        self.records: list[LlmCallRecord] = []

    def record(self, row: LlmCallRecord) -> None:
        self.records.append(row)


class NullLlmCallSink:
    def record(self, row: LlmCallRecord) -> None:
        return None
