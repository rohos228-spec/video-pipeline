"""Временные типы роутера.

TODO(WS3): перенести ``LLMResponse`` / ``LLMUsage`` и строку ``orch_llm_calls``
в ``app/orchestrator/contracts.py``, когда контракт заморозят. Пока WS5 держит
их здесь и не пишет в чужой ``contracts.py``.

``ApprovalEvent`` и ``waiting_approval`` — точные имена WS3. Локальный
dataclass нужен, пока ``contracts.py`` нет на ``main``.

Спецификация таблицы и формат фикстур: ``CONTRACTS.md`` в этом пакете.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0


# Точные имена из WS3 ``app/orchestrator/contracts.py``
# (ветка cursor/ws3-agents-core-r1-3c7d): TaskStatus.waiting_approval,
# ApprovalEvent.reason. Отдельного статуса задачи под бюджет нет.
WAITING_APPROVAL = "waiting_approval"
BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass
class ApprovalEvent:
    """Зеркало WS3 ``ApprovalEvent`` (пока contracts нет на этой ветке).

    Поля и значения совпадают с контрактом: ``task_id``, ``action``,
    ``reason``, ``risk``, ``change_id``, ``payload``.
    """

    task_id: int | None = None
    action: str = ""
    reason: str = ""
    risk: str = ""
    change_id: str | None = None
    payload: dict = field(default_factory=dict)


def waiting_approval_status() -> str:
    """``TaskStatus.waiting_approval``, если контракт уже импортируется."""
    try:
        from app.orchestrator.contracts import TaskStatus
    except (ImportError, AttributeError):
        return WAITING_APPROVAL
    return str(TaskStatus.waiting_approval.value)


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
    fields = {
        "task_id": task_id,
        "action": "",
        "reason": BUDGET_EXHAUSTED,
        "risk": "",
        "change_id": None,
        "payload": payload,
    }
    try:
        from app.orchestrator.contracts import ApprovalEvent as ContractApproval
    except (ImportError, AttributeError):
        ContractApproval = None
    if ContractApproval is not None:
        return ContractApproval(**fields)
    return ApprovalEvent(**fields)


@dataclass
class LLMResponse:
    """Ответ ``router.call``.

    ``task_status`` и ``approval`` — сигнал супервизору WS3, не новый статус
    LLM-вызова. Исчерпание суб-бюджета: ``task_status="waiting_approval"``
    и ``approval.reason="budget_exhausted"``. Статус задачи пишет Supervisor.
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
    task_status: str | None = None
    approval: ApprovalEvent | None = None


@dataclass
class Classification:
    """``router.classify`` → tier, mode, deterministic.

    ``tier`` — логический (его меряет E6). ``effective_tier`` — после флага D6:
    пока D6 не подтверждён, fast исполняется как strong high.

    ``kill_switch`` — поле WS3 ``ClassifyResult`` (ws3-r3). Его ставят
    детерминированные фразы аварийного стопа до LLM. Обычный interrupt
    оставляет ``False``. Локальный shim, пока contracts нет на ``main``.
    """

    tier: str
    mode: str
    deterministic: bool
    confidence: float = 1.0
    reason: str = ""
    effective_tier: str = ""
    kill_switch: bool = False

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
