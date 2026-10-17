"""Фейковый транспорт: скрипт ответов, без сети."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from app.orchestrator.routing.transport import CompletionResult, ProviderRequest


class FakeTransport:
    def __init__(self, script: Sequence[CompletionResult | Exception]) -> None:
        self.script = list(script)
        self.calls: list[ProviderRequest] = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def complete(self, request: ProviderRequest) -> CompletionResult:
        self.calls.append(request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
            if not self.script:
                raise AssertionError("fake transport script exhausted")
            item = self.script.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        finally:
            self.in_flight -= 1


def ok(
    text: str = "ok",
    *,
    model: str = "gpt-5.6-sol",
    prompt_tokens: int = 0,
    completion_tokens: int = 1,
    reasoning_tokens: int = 0,
    finish_reason: str = "stop",
) -> CompletionResult:
    return CompletionResult(
        text=text,
        finish_reason=finish_reason,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
    )
