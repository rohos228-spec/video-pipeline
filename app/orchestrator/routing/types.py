"""Типы роутера.

Поля ``LLMResponse.task_status`` / ``approval``, ``MessageMode.task`` и
``ClassifyResult.kill_switch`` совпадают с WS3 ``contracts.py``
``CONTRACT_VERSION=ws3-r3`` (HEAD ``04a5506a``, ветка
``cursor/ws3-agents-r3-3c7d``). На ``main`` этого модуля ещё нет, поэтому
здесь те же имена и значения. Если модуль импортируется, берутся его классы.

``reason`` и ``effective_tier`` у ``Classification`` в контракте нет.
Спецификация таблицы и фикстур: ``CONTRACTS.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0


class TaskStatus(StrEnum):
    """FSM супервизора. Пишет статусы только Supervisor. Зеркало ws3-r3."""

    queued = "queued"
    planning = "planning"
    critic_review = "critic_review"
    executing = "executing"
    paused = "paused"
    waiting_approval = "waiting_approval"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class MessageMode(StrEnum):
    """Режим входящего сообщения. ``task`` — значение ``MessageMode`` ws3-r3."""

    interrupt = "interrupt"
    steer = "steer"
    append = "append"
    task = "task"


class ApprovalReason(StrEnum):
    """Причина ``waiting_approval``. Зеркало ws3-r3."""

    budget_exhausted = "budget_exhausted"
    risk = "risk"
    critic_escalation = "critic_escalation"


@dataclass
class ApprovalEvent:
    """Поля WS3 ``ApprovalEvent``: типы совпадают с контрактом ``04a5506a``."""

    task_id: int | None = None
    action: str = ""
    reason: str = ""
    risk: str = ""
    change_id: str | None = None
    payload: dict = field(default_factory=dict)


def _contract_attr(name: str):
    try:
        import app.orchestrator.contracts as contracts
    except ImportError:
        return None
    return getattr(contracts, name, None)


def waiting_approval_status() -> TaskStatus:
    """``TaskStatus.waiting_approval`` из контракта, если модуль уже есть."""
    status = _contract_attr("TaskStatus")
    if status is None:
        return TaskStatus.waiting_approval
    return status.waiting_approval


def coerce_mode(value: str, *, kill_switch: bool = False) -> MessageMode:
    """``kill_switch`` важнее обычного режима: всегда ``interrupt``."""
    chosen = "interrupt" if kill_switch else value
    mode = _contract_attr("MessageMode")
    if mode is None:
        return MessageMode(chosen)
    return mode(chosen)


def budget_approval(
    *,
    task_id: int | None,
    spent_usd: float,
    budget_usd: float,
    run_id: str | None,
    task_ref: str | None = None,
) -> ApprovalEvent:
    """``ApprovalEvent`` с ``reason="budget_exhausted"``."""
    payload: dict = {
        "spent_usd": spent_usd,
        "budget_usd": budget_usd,
        "run_id": run_id,
    }
    if task_ref is not None:
        payload["task_ref"] = task_ref
    reason = _contract_attr("ApprovalReason")
    local_reason = ApprovalReason.budget_exhausted.value
    reason_value = local_reason if reason is None else reason.budget_exhausted.value
    fields = {
        "task_id": task_id,
        "action": "",
        "reason": reason_value,
        "risk": "",
        "change_id": None,
        "payload": payload,
    }
    event_cls = _contract_attr("ApprovalEvent") or ApprovalEvent
    return event_cls(**fields)


@dataclass
class LLMResponse:
    """Ответ ``router.call``.

    ``task_status: TaskStatus | None`` и ``approval: ApprovalEvent | None`` —
    как в ``LLMResponse`` ws3-r3. Исчерпание суб-бюджета:
    ``task_status=waiting_approval`` и ``approval.reason="budget_exhausted"``.
    Статус задачи пишет Supervisor. Исключение роутер не бросает.
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
    task_status: TaskStatus | None = None
    approval: ApprovalEvent | None = None


@dataclass
class Classification:
    """``router.classify`` → tier, mode, deterministic.

    ``tier`` — логический (его меряет E6). ``effective_tier`` — после флага D6:
    пока D6 не подтверждён, fast исполняется как strong high.

    ``kill_switch`` — поле ``ClassifyResult``. Если оно true, ``mode``
    всегда ``MessageMode.interrupt`` и важнее обычного interrupt.
    ``reason`` и ``effective_tier`` в контракте нет.
    """

    tier: str
    mode: MessageMode
    deterministic: bool
    confidence: float = 1.0
    reason: str = ""
    effective_tier: str = ""
    kill_switch: bool = False
    task_status: TaskStatus | None = None
    approval: ApprovalEvent | None = None


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
