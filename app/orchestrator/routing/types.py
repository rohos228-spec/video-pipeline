"""Временные типы роутера.

TODO(WS3): перенести ``LLMResponse`` / ``LLMUsage`` и строку ``orch_llm_calls``
в ``app/orchestrator/contracts.py``, когда контракт заморозят. Пока WS5 держит
их здесь и не пишет в чужой ``contracts.py``.

Спецификация таблицы и формат фикстур: ``CONTRACTS.md`` в этом пакете.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class Escalation:
    """D4: внутренний суб-бюджет исчерпан, ран не падает — нужен approval."""

    reason: str
    spent_usd: float
    budget_usd: float
    run_id: str | None
    approval_needed: bool = True


@dataclass
class LLMResponse:
    """Ответ ``router.call``.

    Поле ``escalation`` — расширение поверх согласованного черновика:
    без него WS3 не отличит эскалацию D4 от пустого ответа. См. CONTRACTS.md.
    """

    text: str
    call_id: str
    model: str
    provider: str
    tier: str
    finish_reason: str
    usage: LLMUsage
    cost_usd: float
    latency_ms: int
    fallback_index: int
    deterministic: bool
    fixture_key: str | None = None
    escalation: Escalation | None = None


@dataclass
class Classification:
    """``router.classify`` → tier, mode, deterministic.

    ``tier`` — логический (его меряет E6). ``effective_tier`` — после флага D6:
    пока D6 не подтверждён, fast исполняется как strong high.
    """

    tier: str
    mode: str
    deterministic: bool
    confidence: float = 1.0
    reason: str = ""
    effective_tier: str = ""

    def __getitem__(self, key: str):
        return getattr(self, key)


@dataclass
class LlmCallRecord:
    """Одна строка ``orch_llm_calls`` без ``id`` (его ставит БД).

    Текста промпта и ответа здесь нет.
    """

    call_id: str
    task_id: str | None
    run_id: str | None
    agent_role: str | None
    requested_tier: str
    tier: str
    model: str
    provider: str
    reasoning_effort: str | None
    fallback_index: int
    attempt: int
    status: str
    error: str | None
    deterministic_flag: bool
    classifier_confidence: float | None
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cost_usd: float
    budget_scope: str
    router_impl: str
    messages_sha256: str
    fixture_key: str | None
    created_at: str


class RouterError(Exception):
    """Ошибка роутера, которая не является живым вызовом."""


class FixtureMiss(RouterError):
    """Нет фикстуры. Живой вызов из ReplayRouter запрещён."""

    label = "fixture-miss"

    def __init__(self, fixture_key: str) -> None:
        self.fixture_key = fixture_key
        super().__init__(f"fixture-miss: {fixture_key}")


class RouterExhausted(RouterError):
    """Цепочка моделей исчерпана."""


@dataclass
class CallContext:
    requested_tier: str
    logical_tier: str
    effective_tier: str
    messages: list[dict[str, str]]
    stream: bool
    temperature: float | None
    max_tokens: int | None
    run_id: str | None
    task_id: str | None
    agent_role: str | None
    budget_scope: str
    deterministic: bool
    classifier_confidence: float | None
    on_delta: object | None = None
    messages_sha256: str = ""
    extra: dict = field(default_factory=dict)
