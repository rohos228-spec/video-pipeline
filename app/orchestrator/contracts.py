"""Контракты оркестратора (WS3).

Единая точка типов для WS1–WS6. WS4 генерирует TS из
``export_contract_schemas()`` (JSON Schema моделей + спецификации таблиц).

Спецификация ``node_defs`` / ``node_prompt_versions`` / ``orch_reports`` /
``orch_report_versions`` — от WS2 (владелец node_store).
``orch_llm_calls``, ``LLMResponse`` и ``Chunk`` — от WS5.
``change_id`` — TEXT, как в change_control Action Layer (#342).
Исключение FixtureMiss живёт в routing (WS5), здесь его нет.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "ws3-r3.3"

# История версии контракта. WS4 смотрит ``version`` и этот список.
CONTRACT_CHANGELOG: tuple[tuple[str, str], ...] = (
    ("ws3-r1", "Ядро агентов, SSE, миграции, FSM, таблицы node_defs и orch_*."),
    (
        "ws3-r1.1",
        "ValidatorRunner синхронный, вызов через run_validator. "
        "ModelRouter.call: keyword-only run_id, task_id, agent_role, budget_scope, "
        "reasoning_effort, on_delta. До D6 ходы оркестратора — strong и "
        "reasoning_effort=high. orch_steps.position — монотонный номер попытки, "
        "не индекс плана. CHECK node_key без REGEXP (GLOB).",
    ),
    (
        "ws3-r3",
        "Цикл planner → dry-run → critic → executor. MessageMode.task. "
        "Суб-бюджет budget_exhausted. Риск шага — max(реестр, план).",
    ),
    (
        "ws3-r3.1",
        "internal-вызов без непустого run_id — ValueError; run резервируется до модели. "
        "MessageIn.mode. metadata_of: destructive без approval. "
        "project_id задачи, не 0. Очередь task не стартует рядом с paused. "
        "Исполнитель не зовёт LLM перед действием. "
        "run_validator кладёт run_id и task_id в context. missing_run_id — failed.",
    ),
    (
        "ws3-r3.2",
        "BLOCK_OUTCOMES: budget_exhausted, critic_escalation, missing_project_id "
        "→ waiting_approval; deletion_blocked, missing_run_id, empty_plan и прочий "
        "blocking → failed. Строки revise_limit нет. grant не исполняет удаление "
        "и пустой чекпоинт (empty_plan → failed, без replan). "
        "pipeline без run_id — ValueError, как internal. MessageIn.project_id.",
    ),
    (
        "ws3-r3.3",
        "Planner.make_plan и Critic.review: keyword-only run_id, task_id, "
        "agent_role, budget_scope, reasoning_effort (как у ModelRouter.call; "
        "task_id — str | int | None, роутер приводит к строке). "
        "Грант риска ключуется действием и публичными params и сбрасывается "
        "при save_plan. Цикл исполнения проверяет бюджет на каждой итерации "
        "и перед платным шагом. ok=False — failed, не done и не paid-retry. "
        "read_only и удаление — только реестр (metadata_of, иначе risk_of и "
        "таблица действий); флаги плана только ужесточают. Пока metadata_of "
        "нет, неизвестное имя — destructive. PlanCritic без роутера не approve.",
    ),
)

# До подтверждения D6 классификатор и router.call оркестратора не уходят в fast.
D6_FAST_TIER_ENABLED = False
ORCH_DEFAULT_TIER = "strong"
ORCH_REASONING_EFFORT = "high"

NODE_KEY_RE = re.compile(r"^n_[a-z0-9_]+$")
GROUP_KEY_RE = re.compile(r"^[a-z0-9_]+$")

# Кольцо token-событий и retention orch_events (план §3.2).
TOKEN_RING_SIZE = 1000
EVENT_RETENTION_DAYS = 14
SESSION_EVENT_CAP = 20_000

EPHEMERAL_EVENT_TYPES = frozenset({"token"})
RETAINED_EVENT_TYPES = frozenset({"checkpoint", "approval_required"})

EVENT_TYPES = (
    "ack",
    "token",
    "agent_status",
    "step_started",
    "step_finished",
    "approval_required",
    "checkpoint",
    "validator_result",
    "task_done",
    "task_failed",
    "classification",
    "message",
    "kill_switch",
    "paid_retry_blocked",
    "step_skipped",
)


class TaskStatus(StrEnum):
    """FSM супервизора. Пишет статусы только Supervisor."""

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
    """Режим входящего сообщения (§3.3).

    ``task`` — новая задача, не interrupt/steer/append. Если в сессии уже
    есть активная задача, новая остаётся ``queued`` и не вливается в текущую.
    ``paused`` и ``waiting_approval`` не свободны: вторая задача не стартует
    рядом с ними. Дренаж очереди — только когда активная дошла до терминального
    статуса. ``resume`` / ``grant_approval`` продолжают ту же задачу.
    ``kill_switch`` приходит вместе с ``interrupt`` и обрабатывается как
    аварийный стоп, с приоритетом над обычным interrupt.
    """

    interrupt = "interrupt"
    steer = "steer"
    append = "append"
    task = "task"


class CriticVerdict(StrEnum):
    approve = "approve"
    revise = "revise"
    reject = "reject"


class NodeOrigin(StrEnum):
    db = "db"
    git_override = "git_override"


class NodeStatus(StrEnum):
    active = "active"
    disabled = "disabled"


class NodeSource(StrEnum):
    """Откуда resolve_node собрал определение (§3.6)."""

    git = "git"
    db = "db"


class PromptVersionKind(StrEnum):
    create = "create"
    edit_prompt = "edit_prompt"
    edit_params = "edit_params"
    rollback = "rollback"
    revert_to_git = "revert_to_git"


class PromptCreatedVia(StrEnum):
    """Кто записал версию промпта. Правка из UI студии — не отдельный kind."""

    orchestrator = "orchestrator"
    studio_ui = "studio_ui"


class ApprovalReason(StrEnum):
    """Причина ``waiting_approval``. Нового статуса FSM для бюджета нет.

    Исчерпание суб-бюджета — ``waiting_approval`` + ``budget_exhausted``.
    Статуса ``escalated`` нет. Другие причины можно передать строкой.
    """

    budget_exhausted = "budget_exhausted"
    risk = "risk"
    critic_escalation = "critic_escalation"
    missing_project_id = "missing_project_id"


class Risk(StrEnum):
    """Риск действия. Порядок — от слабого к сильному: план не понижает реестр."""

    none = "none"
    low = "low"
    medium = "medium"
    high = "high"


class ReportKind(StrEnum):
    report = "report"
    nodes_snapshot = "nodes_snapshot"


class AgentRole(StrEnum):
    supervisor = "supervisor"
    planner = "planner"
    critic = "critic"
    executor = "executor"
    classifier = "classifier"
    summarizer = "summarizer"


class ModelTier(StrEnum):
    fast = "fast"
    strong = "strong"
    auto = "auto"


class LLMProvider(StrEnum):
    pay52 = "52pay"
    kie = "kie"


class LLMCallStatus(StrEnum):
    ok = "ok"
    timeout = "timeout"
    error = "error"
    fixture_miss = "fixture_miss"


class BudgetScope(StrEnum):
    internal = "internal"
    pipeline = "pipeline"


class RouterImpl(StrEnum):
    live = "live"
    replay = "replay"
    scripted = "scripted"


class ChunkCorpus(StrEnum):
    rules = "rules"
    history = "history"
    nodes = "nodes"
    prompts = "prompts"


# queued → planning → critic_review → executing → paused|waiting_approval → done|failed|cancelled
FSM_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.queued: frozenset({TaskStatus.planning, TaskStatus.cancelled, TaskStatus.failed}),
    TaskStatus.planning: frozenset(
        {
            TaskStatus.critic_review,
            TaskStatus.waiting_approval,
            TaskStatus.failed,
            TaskStatus.cancelled,
        }
    ),
    TaskStatus.critic_review: frozenset(
        {
            TaskStatus.executing,
            TaskStatus.planning,
            TaskStatus.waiting_approval,
            TaskStatus.failed,
            TaskStatus.cancelled,
        }
    ),
    TaskStatus.executing: frozenset(
        {
            TaskStatus.paused,
            TaskStatus.waiting_approval,
            TaskStatus.planning,
            TaskStatus.done,
            TaskStatus.failed,
            TaskStatus.cancelled,
        }
    ),
    TaskStatus.paused: frozenset(
        {
            TaskStatus.executing,
            TaskStatus.planning,
            TaskStatus.cancelled,
            TaskStatus.failed,
        }
    ),
    TaskStatus.waiting_approval: frozenset(
        {
            TaskStatus.executing,
            TaskStatus.planning,
            TaskStatus.cancelled,
            TaskStatus.failed,
        }
    ),
    TaskStatus.done: frozenset(),
    TaskStatus.failed: frozenset(),
    TaskStatus.cancelled: frozenset(),
}

TERMINAL_STATUSES = frozenset({TaskStatus.done, TaskStatus.failed, TaskStatus.cancelled})


def orch_call_tier(requested: str | None) -> str:
    """Фактический tier хода оркестратора. До D6 всегда ``strong``."""
    if not D6_FAST_TIER_ENABLED:
        return ORCH_DEFAULT_TIER
    return requested or ORCH_DEFAULT_TIER


def usable_project_id(value: int | None) -> int | None:
    """Положительный id проекта. ``0``, отрицательные и не-int не сохраняются."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value <= 0:
        return None
    return value


def orch_run_id(*, task_id: int | None = None, session_id: str | None = None) -> str:
    """Непустой id бакета до вызова модели.

    Задача уже создана — ``orch-task-{id}``. Сообщения до задачи —
    ``orch-session-{session}``, его резервируют в ``accept`` до classify.
    """
    if task_id is not None:
        return f"orch-task-{int(task_id)}"
    session = (session_id or "").strip()
    if not session:
        raise ValueError("internal budget requires run_id")
    return f"orch-session-{session}"


def require_internal_run_id(run_id: str | None, budget_scope: str | None = None) -> None:
    """Платный вызов требует scope ``internal|pipeline`` и непустой ``run_id``.

    Пропуск scope (``None``) считается internal. Явный неизвестный scope —
    ``ValueError`` до проверки ``run_id`` и до вызова модели: набор тот же,
    что у WS5. Пустой id — ошибка, а не нулевой расход. ``pipeline`` без
    атрибуции — та же ошибка, отдельного безымянного бакета нет.
    """
    if budget_scope is not None and budget_scope not in {BudgetScope.internal.value, BudgetScope.pipeline.value}:
        raise ValueError("budget_scope must be internal|pipeline")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("budget requires run_id")


def can_transition(src: TaskStatus, dst: TaskStatus) -> bool:
    return dst in FSM_TRANSITIONS[src]


def validate_node_key(node_key: str) -> str:
    if not NODE_KEY_RE.fullmatch(node_key or ""):
        raise ValueError("node_key должен соответствовать ^n_[a-z0-9_]+$")
    return node_key


class ActionResult(BaseModel):
    """Ответ ``actions.execute`` (WS2).

    ``change_id`` — TEXT из change_control (#342), не числовой ключ.
    """

    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    change_id: str | None = None
    cost: float = 0.0


class ValidatorResult(BaseModel):
    """Ответ ``validators.run`` (WS1).

    В JSON поле называется ``pass`` (как в плане). В Python — ``passed``,
    потому что ``pass`` — ключевое слово.
    """

    model_config = ConfigDict(populate_by_name=True)

    name: str = ""
    passed: bool = Field(default=False, alias="pass", serialization_alias="pass")
    details: Any = ""
    blocking: bool = False


class LLMUsage(BaseModel):
    """Счётчики токенов одного вызова (WS5). Текст промпта сюда не входит."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0


class ApprovalEvent(BaseModel):
    """Событие approval в SSE-шине и в ``LLMResponse.approval`` (WS5).

    Исчерпание суб-бюджета не заводит новый статус FSM: задача остаётся
    ``waiting_approval``, а причина лежит здесь и в payload события
    ``approval_required`` (``reason=budget_exhausted``).
    """

    task_id: int | None = None
    action: str = ""
    reason: str = ""
    risk: str = ""
    change_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    """Ответ ``router.call`` (WS5). Текст промпта не дублируется в ``orch_llm_calls``.

    ``task_status`` и ``approval`` — сигнал супервизору, не статус LLM-вызова.
    Исчерпание суб-бюджета $10: ``task_status=waiting_approval`` и
    ``approval.reason=budget_exhausted``. Текст этого хода сохраняется.
    Роутер при исчерпании исключение не бросает.
    """

    text: str = ""
    call_id: str = ""
    model: str = ""
    provider: str = ""
    tier: str = ""
    finish_reason: str = ""
    usage: LLMUsage = Field(default_factory=LLMUsage)
    cost_usd: float = 0.0
    latency_ms: int = 0
    fallback_index: int = 0
    deterministic: bool = False
    fixture_key: str | None = None
    task_status: TaskStatus | None = None
    approval: ApprovalEvent | None = None


class ChunkMetadata(BaseModel):
    """Поля ``Chunk.metadata`` по спецификации WS5."""

    node_key: str | None = None
    version: int | None = None
    project_id: int | None = None
    updated_at: str | None = None


class Chunk(BaseModel):
    """Чанк retrieval (WS5). ``chunk_id`` — стабильный sha."""

    chunk_id: str
    corpus: ChunkCorpus
    source_id: str = ""
    title: str = ""
    text: str = ""
    score: float = 0.0
    score_bm25: float = 0.0
    score_vec: float = 0.0
    rank: int = 0
    metadata: ChunkMetadata = Field(default_factory=ChunkMetadata)


class NodeDef(BaseModel):
    """Определение ноды, которое отдают ``resolve_node`` и ``get_node`` (WS2).

    ``version`` — версия, которую резолвер считает текущей (git или db).
    ``db_version`` — номер строки в ``node_prompt_versions``, если поверх git
    есть DB-версия; иначе ``None``. ``git_sha256`` — sha256 базового git-файла
    на момент резолва. ``stale_base`` — git-файл изменился после записи
    DB-версии (``base_git_sha256`` версии не совпал с текущим ``git_sha256``).

    Колонка ``node_defs.active_version`` в этот тип не копируется.
    Это единственный указатель активной DB-версии: ``NULL`` значит
    «сейчас как в git» (после ``revert_node_to_git``): ``source=git``,
    ``db_version=None``, ``stale_base=False``. В истории нет ``is_active``.
    """

    node_key: str
    prompt_template: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    source: NodeSource = NodeSource.git
    version: int = 0
    db_version: int | None = None
    git_sha256: str | None = None
    stale_base: bool = False


class OrchEvent(BaseModel):
    """Событие шины. WS4 читает ``type``, ``task_id``, ``payload``, ``seq``.

    ``session_id`` добавлен, чтобы одно событие можно было восстановить
    без внешнего контекста потока.
    """

    type: str
    task_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    seq: int
    session_id: str = ""


class ClassifyResult(BaseModel):
    """Ответ ``router.classify`` (WS5): tier, mode, deterministic.

    ``kill_switch`` — аварийный стоп §3.3: тот же режим interrupt, плюс запрет
    новых действий и взвод стоп-ключа only_node. Поле опционально для WS5.
    """

    tier: str
    mode: MessageMode
    deterministic: bool
    confidence: float = 1.0
    kill_switch: bool = False


class PlanStep(BaseModel):
    """Шаг плана R3: whitelist-действие, параметры, ожидаемый результат.

    ``risk`` — только подсказка планировщика. Approval решает executor
    по max(риск из реестра действий, этот риск): план не может понизить
    реестр. ``parallel_group`` — независимые read-only шаги с одним
    именем группы можно выполнять параллельно.
    ``estimated_cost`` — прогноз spend для критика, не фактическая оплата.
    ``inf`` / ``NaN``, bool и строка-число сюда не кладутся как сумма: это
    неизвестный расход (``params['_cost_unknown']``), вызов блокируется.
    """

    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    expected: str = ""
    paid: bool = False
    mutating: bool = False
    risk: str = "none"
    parallel_group: str | None = None
    estimated_cost: float = Field(default=0.0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _keep_unknown_cost(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        raw_params = data.get("params")
        if raw_params is None:
            params: dict[str, Any] = {}
        elif isinstance(raw_params, dict):
            params = dict(raw_params)
        else:
            return data
        data = dict(data)
        free = data.get("paid", False) is False and data.get("mutating", False) is False
        if free:
            if "estimated_cost" in data and _blank_cost(data.get("estimated_cost")):
                data["estimated_cost"] = 0
            if "estimated_cost" in params and _blank_cost(params.get("estimated_cost")):
                params["estimated_cost"] = 0
                data["params"] = params
        unknown = False
        if "estimated_cost" in data and cost_is_unknown(data.get("estimated_cost")):
            unknown = True
            raw = _json_safe_unknown(data.get("estimated_cost"))
            if "estimated_cost" not in params:
                params["estimated_cost"] = raw
            data["estimated_cost"] = 0
        if "estimated_cost" in params and cost_is_unknown(params.get("estimated_cost")):
            unknown = True
            params["estimated_cost"] = _json_safe_unknown(params.get("estimated_cost"))
        if unknown:
            params["_cost_unknown"] = True
            data["params"] = params
        return data

    @field_validator("estimated_cost", mode="before")
    @classmethod
    def _clamp_estimated_cost(cls, value: Any) -> float:
        if cost_is_unknown(value):
            raise UnknownForecast("unknown spend")
        return nonneg_cost(value)


class SpendSnapshot(BaseModel):
    """Суб-бюджет D4: internal — LLM-ходы оркестратора, actions — пайплайн.

    Лимиты $10/$40 проверяет валидатор WS1, здесь только снимок.
    """

    internal: float = 0.0
    actions: float = 0.0


class CheckpointData(BaseModel):
    """Чекпоинт §3.5: план, позиция, change_id, spend, краткий контекст."""

    plan: list[PlanStep] = Field(default_factory=list)
    position: int = 0
    change_ids: list[str] = Field(default_factory=list)
    spend: SpendSnapshot = Field(default_factory=SpendSnapshot)
    context: str = ""


class ActionMetadata(BaseModel):
    """Метаданные действия из реестра WS2.

    ``read_only=True`` — явное разрешение. Нет метаданных или нет явного
    ``read_only`` — действие потенциально разрушительное.
    ``destructive=True`` — жёсткий запрет, approval его не снимает.
    ``paid=True`` — шаг платный, даже если план сказал ``paid=False``.
    План может только добавить платность, не снять её.
    """

    risk: str = "high"
    read_only: bool | None = None
    destructive: bool = False
    paid: bool = False


class ActionExecutor(Protocol):
    async def execute(self, name: str, params: dict[str, Any], task_ctx: dict[str, Any]) -> ActionResult:
        """WS2: ``actions.execute``.

        WS2 дополнительно проверяет approval внутри ``execute`` и не
        выполняет действие, если риск реестра не подтверждён. Супервизор
        останавливается раньше: ``PlanStep.risk`` — подсказка, порог берётся
        как max(реестр, план).
        """

    def risk_of(self, action_name: str) -> Risk:
        """Риск действия из реестра WS2.

        Неизвестное имя → ``Risk.high`` (``registry_risk``). План не может
        понизить этот риск.
        """

    def metadata_of(self, action_name: str) -> ActionMetadata | None:
        """Метаданные реестра. ``None`` — имя неизвестно.

        Неизвестное имя или отсутствие явного ``read_only=True`` —
        потенциально разрушительное. ``destructive=True`` — запрет без approval.
        """


# Ключ context для dry-run до критика. Согласовано с WS1: валидатор только читает.
VALIDATOR_CONTEXT_DRY_RUN = "dry_run"


class ValidatorRunner(Protocol):
    def run(self, name: str, project_id: int, context: dict[str, Any]) -> ValidatorResult:
        """WS1: синхронный ``validators.run(name, project_id, context) -> ValidatorResult``.

        Async-реализация тоже принимается: ``run_validator`` делает await,
        если ``run`` — корутина или вернул awaitable, иначе вызывает её
        через ``asyncio.to_thread``.

        Перед критиком супервизор передаёт dry-run:
        ``context[VALIDATOR_CONTEXT_DRY_RUN] is True`` (ключ ``dry_run``).
        В этом режиме валидатор только читает и ничего не меняет.
        Live-проверка — до и после мутирующего шага, она может блокировать.
        ``project_id`` берётся из ``orch_tasks.project_id``. Ноль и пустое
        значение не подставляются: нет id проекта — вызова нет.
        Сигнатура и поля результата те же: ``name``, ``pass`` / ``passed``,
        ``details``, ``blocking``.

        ``run_validator`` перед вызовом кладёт в context непустые ``run_id``
        и ``task_id``. Без них вызова нет.

        Исход blocking — ``BLOCK_OUTCOMES`` / ``block_status``: супервизор
        не выбирает статус мимо этой таблицы. ``budget_exhausted`` —
        ``waiting_approval``. ``missing_run_id`` и прочий blocking
        (live и dry-run) — ``failed``.
        """


class ModelRouter(Protocol):
    async def call(
        self,
        tier: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        agent_role: str | None = None,
        budget_scope: str | None = None,
        reasoning_effort: str | None = None,
        on_delta: Any = None,
    ) -> LLMResponse:
        """WS5: ``router.call``.

        ``run_id``, ``task_id``, ``agent_role``, ``budget_scope``,
        ``reasoning_effort``, ``on_delta`` — keyword-only и необязательные.
        ``task_id`` — строка, как у WS5. Собственные вызовы оркестратора
        передают ``budget_scope=internal`` (суб-бюджет $10).
        ``budget_scope=internal`` и ``budget_scope=pipeline`` без непустого
        ``run_id`` — ValueError: ``spent(None)`` не подменяет бакет.

        До D6 tier хода — ``strong``, ``reasoning_effort=high``
        (``ORCH_DEFAULT_TIER`` / ``ORCH_REASONING_EFFORT``).

        ``on_delta(text)`` — боевой стрим WS5: каждый кусок текста. Нарезка
        готового ``response.text`` на слова — заглушка, не контракт UI.
        ``stream=True`` без ``on_delta`` эту заглушку не отменяет.

        После каждого вызова супервизор смотрит ``task_status`` и ``approval``.
        ``waiting_approval`` + ``reason=budget_exhausted`` останавливает задачу:
        чекпоинт, событие ``approval_required``, без следующих internal-вызовов.
        Текст ответа этого хода сохраняется.
        """

    async def classify(
        self,
        message: str,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> ClassifyResult:
        """WS5: ``router.classify``. До D6 дефолт заглушки — tier ``strong``.

        Спорный (LLM) путь — internal-бюджет, поэтому ``run_id`` уже
        зарезервирован. Детерминированные правила роутер не вызывают.
        """


class Retriever(Protocol):
    """WS6: ``rag.retrieve``. Реализация не импортируется, только этот контракт."""

    async def retrieve(
        self,
        query: str,
        corpora: list[str] | None = None,
        top_k: int = 8,
    ) -> list[Chunk]:
        """Чанки правил и истории. Planner кладёт корпус ``rules`` в system."""


class Planner(Protocol):
    async def make_plan(
        self,
        goal: str,
        context: str = "",
        *,
        run_id: str | None = None,
        task_id: str | int | None = None,
        agent_role: str | None = None,
        budget_scope: str | None = None,
        reasoning_effort: str | None = None,
    ) -> list[PlanStep]:
        """План через router + правила из ``rag.retrieve``.

        Keyword-only kwargs — те же, что у ``ModelRouter.call``, кроме
        ``on_delta``. ``task_id`` здесь ``str | int | None``: супервизор
        передаёт int, реализация приводит его к строке перед роутером.
        """


class Critic(Protocol):
    async def review(
        self,
        plan: list[PlanStep],
        context: str = "",
        *,
        run_id: str | None = None,
        task_id: str | int | None = None,
        agent_role: str | None = None,
        budget_scope: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        """Вердикт approve | revise | reject.

        Keyword-only kwargs совпадают с ``Planner.make_plan``.
        """


class StepExecutor(Protocol):
    async def run(self, step: PlanStep, ctx: dict[str, Any]) -> ActionResult:
        """Один шаг только через ``actions.execute``."""


# Правило 9: перед run_node обязателен verify_code_state
# (read_pipeline_code + get_node). Имена действий — мини-RFC для WS1/WS2.
RUN_NODE_ACTIONS: frozenset[str] = frozenset({"run_node", "node.run_step", "step.run_step"})
DELETION_ACTIONS: frozenset[str] = frozenset(
    {
        "montage.delete_frame",
        "montage.delete_child_frame",
        "montage.delete_image",
        "montage.delete_video",
        "montage.delete_manual_ref",
        "group.delete",
        "prompt.delete_master",
        "delete_child_frame",
    }
)
APPROVAL_RISKS: frozenset[str] = frozenset({"medium", "high"})
PIPELINE_FORECAST_LIMIT_USD = 40.0
_RISK_RANK: dict[Risk, int] = {Risk.none: 0, Risk.low: 1, Risk.medium: 2, Risk.high: 3}


def coerce_risk(value: Any) -> Risk:
    """Строка плана или значение реестра. Неизвестная метка → ``high``."""
    text = str(getattr(value, "value", value) or "").strip().lower()
    try:
        return Risk(text)
    except ValueError:
        return Risk.high


def registry_risk(action_name: str, registry: dict[str, Any] | None) -> Risk:
    """Риск из реестра действий. Имени нет в реестре → ``Risk.high``."""
    if not registry or action_name not in registry:
        return Risk.high
    return coerce_risk(registry[action_name])


def max_risk(*values: Any) -> Risk:
    """Больший из рисков. Пустой набор → ``high``."""
    if not values:
        return Risk.high
    return max((coerce_risk(item) for item in values), key=lambda item: _RISK_RANK[item])


def validator_details_reason(result: Any) -> str | None:
    """``details.reason``, если details — dict или объект с полем reason."""
    details = getattr(result, "details", None)
    reason = details.get("reason") if isinstance(details, dict) else getattr(details, "reason", None)
    if reason is None:
        return None
    return str(reason)


def validator_budget_exhausted(result: Any) -> bool:
    """Блокирующий ответ валидатора из-за суб-бюджета, не обычный fail.

    ``blocking`` истинно и ``details.reason == "budget_exhausted"``.
    ``details`` — dict или объект с полем ``reason``.
    """
    if not bool(getattr(result, "blocking", False)):
        return False
    return validator_details_reason(result) == ApprovalReason.budget_exhausted.value


def validator_missing_run_id(result: Any) -> bool:
    """Блокирующий spend_guard: в live-context не было run_id.

    Исход — ``BLOCK_OUTCOMES["missing_run_id"]`` (``failed``).
    Dry-run WS1 эту причину не ставит.
    """
    if not bool(getattr(result, "blocking", False)):
        return False
    return validator_details_reason(result) == "missing_run_id"


# Причина → исход. Супервизор ставит статус блокировки только через block_status.
# Строки revise_limit нет: лимит revise — critic_escalation.
# plan_sanity, critic_reject, steer_not_approved и unknown_cost — waiting_approval.
# Грант до approve критика не исполняет чекпоинт, а возвращает задачу в planning.
# empty_plan — пустой план чекпоинта на grant/resume: failed, без replan.
# unknown_cost — строка, bool, None, inf, NaN или отрицательное в прогнозе: не 0.
BLOCK_OUTCOMES: dict[str, TaskStatus] = {
    ApprovalReason.budget_exhausted.value: TaskStatus.waiting_approval,
    ApprovalReason.critic_escalation.value: TaskStatus.waiting_approval,
    ApprovalReason.missing_project_id.value: TaskStatus.waiting_approval,
    "paid_unknown_outcome": TaskStatus.waiting_approval,
    "plan_sanity": TaskStatus.waiting_approval,
    "critic_reject": TaskStatus.waiting_approval,
    "steer_not_approved": TaskStatus.waiting_approval,
    "unknown_cost": TaskStatus.waiting_approval,
    "paid_outcome_unknown": TaskStatus.waiting_approval,
    "deletion_blocked": TaskStatus.failed,
    "missing_run_id": TaskStatus.failed,
    "empty_plan": TaskStatus.failed,
}


def block_status(reason: str) -> TaskStatus:
    """Исход блокировки. Причины нет в ``BLOCK_OUTCOMES`` — ``failed`` (прочий blocking)."""
    return BLOCK_OUTCOMES.get(reason, TaskStatus.failed)


def grant_forbidden(reason: str | None) -> bool:
    """Терминальная причина из таблицы: grant этот план не исполняет."""
    if reason is None or reason not in BLOCK_OUTCOMES:
        return False
    return BLOCK_OUTCOMES[reason] in TERMINAL_STATUSES


def budget_exhausted_response(response: Any) -> bool:
    """Ответ роутера, после которого супервизор больше не делает internal-вызовы.

    Условие WS5: ``task_status=waiting_approval`` и ``approval.reason=budget_exhausted``.
    """
    status = getattr(response, "task_status", None)
    status_value = getattr(status, "value", status)
    approval = getattr(response, "approval", None)
    reason = getattr(approval, "reason", "") if approval is not None else ""
    return (
        status_value == TaskStatus.waiting_approval.value
        and reason == ApprovalReason.budget_exhausted.value
    )


# Сегмент, не подстрока: ``dropdown`` не содержит ``drop``.
_DELETION_TOKENS = frozenset(
    {
        "delete",
        "remove",
        "drop",
        "purge",
        "clear",
        "wipe",
        "truncate",
        "prune",
        "reset",
        "destroy",
        "rename",
        "replace",
        "overwrite",
        "erase",
        "unlink",
        "del",
        "rm",
    }
)
# Сцены, кадры, персонажи и VO не удаляются никогда.
_PROTECTED_OBJECTS = frozenset(
    {
        "frame",
        "frames",
        "scene",
        "scenes",
        "shot",
        "shots",
        "kadr",
        "kadry",
        "kadrs",
        "character",
        "characters",
        "hero",
        "heroes",
        "vo",
        "voiceover",
        "voiceovers",
    }
)


def _text_tokens(value: str) -> set[str]:
    """Сегменты имени. camelCase делится до lower, ``del`` и ``rm`` — целые токены.

    ``model`` не содержит ``del``, ``form`` не содержит ``rm``.
    """
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value or "")
    return {part for part in re.split(r"[^a-z0-9]+", split.lower()) if part}


def is_named_deletion(action: str) -> bool:
    """Маркер удаления в имени. Метаданные реестра его не снимают.

    Список системных вызовов не ведётся: срабатывает сегмент
    (``delete``/``remove``/… и SQL/HTTP ``DELETE``/``DROP`` как слово).
    """
    return is_deletion_action(action)


def is_deletion_verb(action: str) -> bool:
    """Сегмент из списка запрета. ``dropdown`` сегмента ``drop`` не содержит."""
    return bool(_text_tokens(action or "") & _DELETION_TOKENS)


def is_deletion_action(action: str) -> bool:
    """Имя с маркером удаления или точное действие из ``DELETION_ACTIONS``."""
    name = (action or "").strip()
    if not name:
        return False
    if name in DELETION_ACTIONS:
        return True
    return bool(_text_tokens(name) & _DELETION_TOKENS)


_HTTP_PARAM_KEYS = frozenset({"method", "httpmethod", "httpverb", "verb"})
_SQL_PARAM_KEYS = frozenset({"sql", "query", "statement", "statements"})
_KNOWN_HTTP_METHODS = frozenset(
    {"get", "head", "options", "post", "put", "patch", "delete", "trace", "connect"}
)


def _param_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(key).lower())


def _computed_text(value: str) -> bool:
    """Шаблон или подстановка: литерал метода или SQL доказать нельзя."""
    return any(mark in value for mark in ("{", "}", "${", "`"))


def _http_method_unproven(value: Any) -> bool:
    """Метод не литерал, вычисляется или не из известного списка."""
    if isinstance(value, bool) or not isinstance(value, str):
        return True
    if _computed_text(value):
        return True
    method = value.strip().lower()
    if method not in _KNOWN_HTTP_METHODS:
        return True
    return bool(_text_tokens(method) & _DELETION_TOKENS)


def _sql_unproven(value: Any) -> bool:
    """SQL не литерал, вычисляется, либо содержит удаление, ``TRUNCATE`` или ``executemany``."""
    if isinstance(value, bool) or not isinstance(value, str):
        return True
    if _computed_text(value):
        return True
    tokens = _text_tokens(value)
    return bool(tokens & _DELETION_TOKENS or "executemany" in tokens)


_OP_PARAM_KEYS = frozenset({"op", "action", "operation"})


def _is_sql_action(action: str) -> bool:
    return "sql" in _text_tokens(action or "")


def _op_requests_deletion(value: Any) -> bool:
    """``op`` / ``action`` блокируют только глагол удаления, не свободный текст."""
    if isinstance(value, bool) or not isinstance(value, str):
        return False
    return bool(_text_tokens(value) & _DELETION_TOKENS)


def params_request_deletion(value: Any, *, action: str = "") -> bool:
    """Структурные ключи, не свободный текст промпта.

    ``method`` — HTTP. ``op`` / ``action`` / ``operation`` — глагол.
    ``sql`` / ``query`` / ``statement`` смотрятся только у SQL-действия.
    Нелитеральный HTTP-метод по-прежнему ``deletion_blocked``.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            kind = _param_key(key)
            if kind in _HTTP_PARAM_KEYS and _http_method_unproven(item):
                return True
            if kind in _OP_PARAM_KEYS and _op_requests_deletion(item):
                return True
            if _is_sql_action(action) and kind in _SQL_PARAM_KEYS and _sql_unproven(item):
                return True
            if isinstance(item, (dict, list, tuple)) and params_request_deletion(item, action=action):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(params_request_deletion(item, action=action) for item in value)
    return False


def _protects_media(action: str) -> bool:
    """Сцена, кадр, персонаж или VO вместе с глаголом удаления."""
    parts = _text_tokens(action or "")
    return bool(parts & _DELETION_TOKENS and parts & _PROTECTED_OBJECTS)


_MISSING = object()


def action_unknown(action: str, registry: Any | None) -> bool:
    """Имени нет в реестре.

    ``metadata_of`` — источник, когда метод есть: ``None`` значит неизвестно.
    Пока его нет (R2): закрытый словарь ``risks`` без ключа, реестр без
    ``risk_of``, или реестра нет вовсе. Открытый мок (``risks is None``)
    считает любое имя известным как ``Risk.none`` — тестовые планы живут.
    """
    if registry is None:
        return True
    metadata_of = getattr(registry, "metadata_of", None)
    if metadata_of is not None:
        return metadata_of(action) is None
    risks = getattr(registry, "risks", _MISSING)
    if risks is None:
        return False
    if isinstance(risks, dict):
        return action not in risks
    return True


def explicitly_safe(action: str, registry: Any | None) -> bool:
    """В реестре есть явные ``read_only=True`` и ``destructive=False``.

    Иначе запрет по умолчанию. Открытый мок (``risks is None``, нет
    ``metadata_of``) не закрытый реестр: имя без маркера удаления живёт.
    Закрытый словарь без ключа безопасным не считается.
    """
    if registry is None:
        return False
    metadata_of = getattr(registry, "metadata_of", None)
    if metadata_of is not None:
        meta = metadata_of(action)
        return (
            meta is not None
            and getattr(meta, "read_only", None) is True
            and not bool(getattr(meta, "destructive", False))
        )
    risks = getattr(registry, "risks", _MISSING)
    if risks is None:
        return True
    if isinstance(risks, dict):
        return action in risks
    return False


def action_guard(action: str, registry: Any | None = None) -> str | None:
    """``name`` — ``deletion_blocked``. ``None`` — действие можно планировать.

    Запрет по умолчанию: нет явных ``read_only`` и не-destructive, либо в имени
    маркер удаления. Флаги плана это не ослабляют. Сцены, кадры, персонажи и
    VO с глаголом удаления не оправдываются метаданными.
    """
    if is_deletion_action(action) or _protects_media(action) or not explicitly_safe(action, registry):
        return "name"
    return None


def _metadata_destructive(meta: Any) -> bool:
    """``destructive=True`` и у объекта, и у словаря метаданных."""
    if isinstance(meta, dict):
        return meta.get("destructive") is True
    return getattr(meta, "destructive", False) is True


def step_is_deletion(step: PlanStep, registry: Any | None = None) -> bool:
    """Удаление: маркер имени или параметров, ``destructive=True`` или неизвестное имя.

    Известная мутация (``read_only`` ложь, ``destructive`` ложь) удалением не
    считается: её останавливает риск, а не ``deletion_blocked``.
    """
    if (
        is_deletion_action(step.action)
        or _protects_media(step.action)
        or params_request_deletion(step.params, action=step.action)
    ):
        return True
    if action_unknown(step.action, registry):
        return True
    metadata_of = getattr(registry, "metadata_of", None) if registry is not None else None
    if metadata_of is None:
        return False
    meta = metadata_of(step.action)
    return meta is not None and _metadata_destructive(meta)


def registry_is_readonly(action: str, registry: Any | None) -> bool:
    """Read-only решает реестр, не план.

    Удаление и имя без явного ``read_only`` — не read-only. Риск реестра из
    ``APPROVAL_RISKS`` тоже не read-only. Открытый мок без метаданных и с
    ``Risk.none`` — read-only, если в имени нет маркера удаления.
    """
    if is_deletion_action(action) or _protects_media(action) or not explicitly_safe(action, registry):
        return False
    risk_of = getattr(registry, "risk_of", None) if registry is not None else None
    if risk_of is None:
        return True
    return coerce_risk(risk_of(action)) not in APPROVAL_RISKS


def step_is_readonly(step: PlanStep, registry: Any | None) -> bool:
    """Реестр решает read-only. ``paid`` / ``mutating`` / риск плана только ужесточают."""
    if not registry_is_readonly(step.action, registry):
        return False
    if step.paid or step.mutating:
        return False
    return coerce_risk(step.risk) not in APPROVAL_RISKS


def plan_deletions(plan: list[PlanStep]) -> list[str]:
    """Маркеры в имени и параметрах. Запрет по реестру смотрит супервизор."""
    return [
        step.action
        for step in plan
        if is_deletion_action(step.action)
        or _protects_media(step.action)
        or params_request_deletion(step.params, action=step.action)
    ]


def plan_missing_verify(plan: list[PlanStep]) -> bool:
    """True, если run_node стоит раньше verify_code_state (или пары read+get_node)."""
    for index, step in enumerate(plan):
        if step.action not in RUN_NODE_ACTIONS:
            continue
        prior = plan[:index]
        if any(item.action == "verify_code_state" for item in prior):
            continue
        seen = {item.action for item in prior}
        if "read_pipeline_code" in seen and "get_node" in seen:
            continue
        return True
    return False


def _blank_cost(value: Any) -> bool:
    """Пустая оценка бесплатного шага. У платного и мутирующего это по-прежнему неизвестный расход."""
    return value is None or value == ""


class UnknownForecast(ValueError):
    """Прогноз шага — неизвестный расход. В 0 не превращается, вызов блокируется."""


def cost_is_unknown(value: Any) -> bool:
    """Строка, bool, None, inf, NaN, отрицательное. В 0 не превращается."""
    if value is None or isinstance(value, (bool, str)):
        return True
    if isinstance(value, (int, float)):
        number = float(value)
        return (not math.isfinite(number)) or number < 0
    return True


def _json_safe_unknown(value: Any) -> Any:
    """Нефинитный float нельзя класть в JSON чекпоинта. Смысл «не число» сохраняется."""
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            return "nan"
        return "inf" if value > 0 else "-inf"
    return value


def nonneg_cost(value: Any) -> float:
    """Конечное число ``>= 0``. Всё остальное — ``UnknownForecast``, не 0."""
    if cost_is_unknown(value):
        raise UnknownForecast("unknown spend")
    return float(value)


def ceiling_blocks(limit: Any) -> bool:
    """Потолок ``<= 0``, строка, bool, None, inf и NaN блокируют вызов.

    Ноль не выключает проверку.
    """
    if isinstance(limit, bool) or limit is None or isinstance(limit, str):
        return True
    if isinstance(limit, (int, float)):
        number = float(limit)
        return (not math.isfinite(number)) or number <= 0
    return True


def server_forecast_limit() -> float:
    """Потолок только из конфига ядра, не из плана, контекста и сообщения."""
    return float(PIPELINE_FORECAST_LIMIT_USD)


_CLIENT_MONEY_KEYS = frozenset(
    {
        "spent",
        "spent_usd",
        "spent_internal",
        "spent_actions",
        "limit",
        "limit_usd",
        "budget",
        "budget_usd",
        "ceiling",
        "forecast_limit",
        "budget_limit",
    }
)


def plan_without_client_money(plan: list[PlanStep]) -> list[dict[str, Any]]:
    """В контекст валидатора не кладём потраченное и потолок из плана."""
    dumped: list[dict[str, Any]] = []
    for step in plan:
        raw = step.model_dump()
        params = dict(raw.get("params") or {})
        for key in list(params):
            if str(key) in _CLIENT_MONEY_KEYS:
                params.pop(key, None)
        raw["params"] = params
        dumped.append(raw)
    return dumped


def forecast_unknown(plan: list[PlanStep]) -> bool:
    """В плане есть неизвестный расход: прогноз нельзя подменить нулём."""
    return any(_step_cost_unknown(step) for step in plan)


def _step_cost_unknown(step: PlanStep) -> bool:
    if step.params.get("_cost_unknown"):
        return True
    if isinstance(step.estimated_cost, float) and not math.isfinite(step.estimated_cost):
        return True
    return "estimated_cost" in step.params and cost_is_unknown(step.params.get("estimated_cost"))


def forecast_cost(plan: list[PlanStep]) -> float:
    """Прогноз spend. Поле и ``params.estimated_cost`` не суммируются дважды.

    Консервативно берётся большее из двух конечных чисел. Строка, bool, None,
    inf, NaN и отрицательное — ``UnknownForecast``, не 0.
    """
    if forecast_unknown(plan):
        raise UnknownForecast("unknown spend")
    total = 0.0
    for step in plan:
        present: list[float] = []
        if step.estimated_cost:
            present.append(nonneg_cost(step.estimated_cost))
        if "estimated_cost" in step.params and not step.params.get("_cost_unknown"):
            present.append(nonneg_cost(step.params["estimated_cost"]))
        if present:
            total += max(present)
    return total


def paid_step_identity(action: str, params: dict[str, Any]) -> str:
    """Ключ paid-retry: то же действие и те же параметры без служебных ``_``-полей."""
    kept = {key: value for key, value in params.items() if not str(key).startswith("_")}
    return action + "\n" + json.dumps(kept, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    sql_type: str
    primary_key: bool = False
    nullable: bool = True
    unique: bool = False
    references: str | None = None
    check: str | None = None
    default: str | None = None
    note: str = ""


@dataclass(frozen=True)
class IndexSpec:
    name: str
    columns: tuple[str, ...]
    unique: bool = False
    where: str | None = None


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[ColumnSpec, ...]
    unique: tuple[tuple[str, ...], ...] = ()
    indexes: tuple[IndexSpec, ...] = ()
    notes: str = ""
    immutable: bool = False

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["columns"] = [asdict(c) for c in self.columns]
        raw["unique"] = [list(u) for u in self.unique]
        raw["indexes"] = [asdict(i) | {"columns": list(i.columns)} for i in self.indexes]
        return raw


# Время во всех таблицах — ISO-строка UTC (TEXT).


NODE_DEFS = TableSpec(
    name="node_defs",
    notes=(
        "Спецификация WS2. Одна строка на ноду, созданную или переопределённую "
        "оркестратором. active_version — единственный источник активной версии "
        "(NULL = git-шаблон). Триггер проверяет, что не-NULL ссылается на "
        "существующую версию этой ноды и не на kind=revert_to_git. "
        "Циклический FK не ставим."
    ),
    columns=(
        ColumnSpec("id", "INTEGER", primary_key=True, nullable=False),
        ColumnSpec(
            "node_key",
            "TEXT",
            nullable=False,
            unique=True,
            check="length>=3 AND GLOB n_[a-z0-9_]* AND NOT GLOB *[^a-z0-9_]*",
            note="UNIQUE, формат ^n_[a-z0-9_]+$ через GLOB, без REGEXP",
        ),
        ColumnSpec("group_key", "TEXT", nullable=False),
        ColumnSpec("title", "TEXT", nullable=True, check="length <= 120"),
        ColumnSpec(
            "origin",
            "TEXT",
            nullable=False,
            check="db|git_override",
        ),
        ColumnSpec("is_new_group", "INTEGER", nullable=False, default="0", note="BOOL"),
        ColumnSpec("after_node_key", "TEXT", nullable=True),
        ColumnSpec("status", "TEXT", nullable=False, default="active", check="active|disabled"),
        ColumnSpec(
            "active_version",
            "INTEGER",
            nullable=True,
            note=(
                "Единственный указатель активной версии: node_prompt_versions.version "
                "этой ноды, не kind=revert_to_git. NULL — резолвер берёт git "
                "(revert_node_to_git). Статус reverted не вводим: active|disabled "
                "описывают включённость, а не источник промпта."
            ),
        ),
        ColumnSpec("created_change_id", "TEXT", nullable=True),
        ColumnSpec("updated_change_id", "TEXT", nullable=True),
        ColumnSpec("created_at", "TEXT", nullable=False, note="ISO UTC"),
        ColumnSpec("updated_at", "TEXT", nullable=False, note="ISO UTC"),
        ColumnSpec("created_by", "TEXT", nullable=True, note="task_id или user"),
    ),
)


NODE_PROMPT_VERSIONS = TableSpec(
    name="node_prompt_versions",
    immutable=True,
    notes=(
        "Спецификация WS2. Неизменяемая история: откат — новая строка "
        "kind=rollback, возврат на git-шаблон — новая строка kind=revert_to_git "
        "(действие revert_node_to_git) и node_defs.active_version=NULL. "
        "Старые строки не удаляются и не правятся. Колонки is_active нет: "
        "какая версия активна, решает только node_defs.active_version. "
        "base_git_sha256 — sha256 git-файла на момент записи версии; "
        "по нему resolve_node считает NodeDef.stale_base. "
        "Правка из UI студии — created_via=studio_ui, не отдельный kind."
    ),
    columns=(
        ColumnSpec("id", "INTEGER", primary_key=True, nullable=False),
        ColumnSpec(
            "node_key",
            "TEXT",
            nullable=False,
            references="node_defs.node_key",
        ),
        ColumnSpec("version", "INTEGER", nullable=False, note="монотонно внутри ноды"),
        ColumnSpec("prompt_template", "TEXT", nullable=False),
        ColumnSpec(
            "params_json",
            "TEXT",
            nullable=False,
            note="model, reasoning_effort, parallel, stagger_sec, temperature, max_tokens",
        ),
        ColumnSpec(
            "kind",
            "TEXT",
            nullable=False,
            check="create|edit_prompt|edit_params|rollback|revert_to_git",
        ),
        ColumnSpec("patch_mode", "TEXT", nullable=True),
        ColumnSpec("section_anchor", "TEXT", nullable=True),
        ColumnSpec("reason", "TEXT", nullable=True),
        ColumnSpec("parent_version", "INTEGER", nullable=True),
        ColumnSpec(
            "base_git_sha256",
            "TEXT",
            nullable=True,
            note="sha256 базового git-файла на момент записи версии; NULL если базы не было",
        ),
        ColumnSpec("change_id", "TEXT", nullable=False),
        ColumnSpec("created_at", "TEXT", nullable=False, note="ISO UTC"),
        ColumnSpec("created_by", "TEXT", nullable=True),
        ColumnSpec(
            "created_via",
            "TEXT",
            nullable=False,
            default="orchestrator",
            check="orchestrator|studio_ui",
            note="orchestrator | studio_ui",
        ),
    ),
    unique=(("node_key", "version"),),
)


ORCH_REPORTS = TableSpec(
    name="orch_reports",
    notes="Спецификация WS2.",
    columns=(
        ColumnSpec("id", "INTEGER", primary_key=True, nullable=False),
        ColumnSpec("project_id", "INTEGER", nullable=False),
        ColumnSpec("slug", "TEXT", nullable=True),
        ColumnSpec("title", "TEXT", nullable=True),
        ColumnSpec("file_path", "TEXT", nullable=True),
        ColumnSpec("editable", "INTEGER", nullable=False, default="1", note="BOOL"),
        ColumnSpec("version", "INTEGER", nullable=False, default="1"),
        ColumnSpec("kind", "TEXT", nullable=False, check="report|nodes_snapshot"),
        ColumnSpec("created_change_id", "TEXT", nullable=True),
        ColumnSpec("updated_change_id", "TEXT", nullable=True),
        ColumnSpec("created_at", "TEXT", nullable=False, note="ISO UTC"),
        ColumnSpec("updated_at", "TEXT", nullable=False, note="ISO UTC"),
    ),
    unique=(("project_id", "slug"),),
)


ORCH_REPORT_VERSIONS = TableSpec(
    name="orch_report_versions",
    notes="Спецификация WS2.",
    columns=(
        ColumnSpec("id", "INTEGER", primary_key=True, nullable=False),
        ColumnSpec("report_id", "INTEGER", nullable=False, references="orch_reports.id"),
        ColumnSpec("version", "INTEGER", nullable=False),
        ColumnSpec("sections_json", "TEXT", nullable=True),
        ColumnSpec("html_sha256", "TEXT", nullable=True),
        ColumnSpec("change_id", "TEXT", nullable=True),
        ColumnSpec("created_at", "TEXT", nullable=False, note="ISO UTC"),
    ),
    unique=(("report_id", "version"),),
)


ORCH_TASKS = TableSpec(
    name="orch_tasks",
    notes="Задача супервизора. Статус пишет только Supervisor.",
    columns=(
        ColumnSpec("id", "INTEGER", primary_key=True, nullable=False),
        ColumnSpec("session_id", "TEXT", nullable=False),
        ColumnSpec("status", "TEXT", nullable=False),
        ColumnSpec("goal", "TEXT", nullable=True),
        ColumnSpec("plan_json", "TEXT", nullable=True),
        ColumnSpec("position", "INTEGER", nullable=False, default="0"),
        ColumnSpec("spend_internal", "REAL", nullable=False, default="0"),
        ColumnSpec("spend_actions", "REAL", nullable=False, default="0"),
        ColumnSpec("cancel_requested", "INTEGER", nullable=False, default="0", note="BOOL"),
        ColumnSpec("pause_requested", "INTEGER", nullable=False, default="0", note="BOOL"),
        ColumnSpec("steer_text", "TEXT", nullable=True),
        ColumnSpec("error", "TEXT", nullable=True),
        ColumnSpec("created_at", "TEXT", nullable=False, note="ISO UTC"),
        ColumnSpec("updated_at", "TEXT", nullable=False, note="ISO UTC"),
        ColumnSpec("project_id", "INTEGER", nullable=True, note="проект WS1, не 0"),
    ),
    indexes=(IndexSpec("ix_orch_tasks_session", ("session_id",)),),
)


ORCH_STEPS = TableSpec(
    name="orch_steps",
    notes=(
        "Попытка шага. position — монотонный номер попытки внутри задачи "
        "(0, 1, 2, …), не индекс плана и не переиспользуется. "
        "Индекс следующего шага текущего плана — orch_tasks.position "
        "и orch_checkpoints.position. Payload step_started.position — индекс плана."
    ),
    columns=(
        ColumnSpec("id", "INTEGER", primary_key=True, nullable=False),
        ColumnSpec("task_id", "INTEGER", nullable=False, references="orch_tasks.id"),
        ColumnSpec("position", "INTEGER", nullable=False),
        ColumnSpec("action_name", "TEXT", nullable=False),
        ColumnSpec("params_json", "TEXT", nullable=False, default="{}"),
        ColumnSpec("expected", "TEXT", nullable=True),
        ColumnSpec("paid", "INTEGER", nullable=False, default="0", note="BOOL"),
        ColumnSpec("mutating", "INTEGER", nullable=False, default="0", note="BOOL"),
        ColumnSpec("status", "TEXT", nullable=False),
        ColumnSpec("change_id", "TEXT", nullable=True),
        ColumnSpec("cost", "REAL", nullable=False, default="0"),
        ColumnSpec("started_at", "TEXT", nullable=True, note="ISO UTC"),
        ColumnSpec("finished_at", "TEXT", nullable=True, note="ISO UTC"),
    ),
    unique=(("task_id", "position"),),
)


ORCH_CHECKPOINTS = TableSpec(
    name="orch_checkpoints",
    notes="Снимок {план, позиция, change_id, spend, краткий контекст}.",
    columns=(
        ColumnSpec("id", "INTEGER", primary_key=True, nullable=False),
        ColumnSpec("task_id", "INTEGER", nullable=False, references="orch_tasks.id"),
        ColumnSpec("session_id", "TEXT", nullable=False),
        ColumnSpec("plan_json", "TEXT", nullable=False),
        ColumnSpec("position", "INTEGER", nullable=False),
        ColumnSpec("change_ids_json", "TEXT", nullable=False, default="[]"),
        ColumnSpec("spend_json", "TEXT", nullable=False, default="{}"),
        ColumnSpec("context", "TEXT", nullable=False, default=""),
        ColumnSpec("created_at", "TEXT", nullable=False, note="ISO UTC"),
    ),
    indexes=(IndexSpec("ix_orch_checkpoints_task", ("task_id", "id")),),
)


ORCH_EVENTS = TableSpec(
    name="orch_events",
    notes=(
        "Укрупнённые события. token сюда не пишется (кольцо в памяти). "
        "seq монотонен на сессию. orch_event_seq хранит верхнюю отметку: "
        "номера токенов резервируются пачкой в памяти и не пишут БД по одному."
    ),
    columns=(
        ColumnSpec("id", "INTEGER", primary_key=True, nullable=False),
        ColumnSpec("session_id", "TEXT", nullable=False),
        ColumnSpec("task_id", "INTEGER", nullable=True),
        ColumnSpec("seq", "INTEGER", nullable=False),
        ColumnSpec("type", "TEXT", nullable=False),
        ColumnSpec("payload_json", "TEXT", nullable=False, default="{}"),
        ColumnSpec("created_at", "TEXT", nullable=False, note="ISO UTC"),
    ),
    unique=(("session_id", "seq"),),
    indexes=(IndexSpec("ix_orch_events_session_created", ("session_id", "created_at")),),
)


ORCH_EVENT_SEQ = TableSpec(
    name="orch_event_seq",
    notes="Монотонный seq на сессию. Растёт и для token-событий, чтобы seq не переиспользовался.",
    columns=(
        ColumnSpec("session_id", "TEXT", primary_key=True, nullable=False),
        ColumnSpec("seq", "INTEGER", nullable=False),
    ),
)


ORCH_MESSAGES = TableSpec(
    name="orch_messages",
    notes="История чата для RAG (WS6 индексирует). Очередь R1 пишет сюда принятые реплики.",
    columns=(
        ColumnSpec("id", "INTEGER", primary_key=True, nullable=False),
        ColumnSpec("session_id", "TEXT", nullable=False),
        ColumnSpec("task_id", "INTEGER", nullable=True),
        ColumnSpec("role", "TEXT", nullable=False),
        ColumnSpec("content", "TEXT", nullable=False),
        ColumnSpec("mode", "TEXT", nullable=True, note="interrupt|steer|append|task"),
        ColumnSpec("created_at", "TEXT", nullable=False, note="ISO UTC"),
    ),
    indexes=(IndexSpec("ix_orch_messages_session", ("session_id", "id")),),
)


ORCH_LLM_CALLS = TableSpec(
    name="orch_llm_calls",
    notes=(
        "Спецификация WS5. Одна строка на попытку вызова. "
        "Текст промптов и ответов не хранится. task_id/run_id — TEXT. "
        "change_id в других таблицах тоже TEXT (#342)."
    ),
    columns=(
        ColumnSpec("id", "INTEGER", primary_key=True, nullable=False),
        ColumnSpec("call_id", "TEXT", nullable=False, unique=True, note="uuid"),
        ColumnSpec("task_id", "TEXT", nullable=True),
        ColumnSpec("run_id", "TEXT", nullable=True),
        ColumnSpec(
            "agent_role",
            "TEXT",
            nullable=False,
            check="supervisor|planner|critic|executor|classifier|summarizer",
        ),
        ColumnSpec("requested_tier", "TEXT", nullable=False, check="fast|strong|auto"),
        ColumnSpec("tier", "TEXT", nullable=False, check="fast|strong|auto"),
        ColumnSpec("model", "TEXT", nullable=False),
        ColumnSpec("provider", "TEXT", nullable=False, check="52pay|kie"),
        ColumnSpec("reasoning_effort", "TEXT", nullable=True),
        ColumnSpec("fallback_index", "INTEGER", nullable=False, default="0", note="0 = основная модель"),
        ColumnSpec("attempt", "INTEGER", nullable=False, default="1"),
        ColumnSpec(
            "status",
            "TEXT",
            nullable=False,
            check="ok|timeout|error|fixture_miss",
        ),
        ColumnSpec("error", "TEXT", nullable=True),
        ColumnSpec("deterministic_flag", "INTEGER", nullable=False, note="BOOL"),
        ColumnSpec("classifier_confidence", "REAL", nullable=True),
        ColumnSpec("latency_ms", "INTEGER", nullable=False),
        ColumnSpec("prompt_tokens", "INTEGER", nullable=False),
        ColumnSpec("completion_tokens", "INTEGER", nullable=False),
        ColumnSpec("reasoning_tokens", "INTEGER", nullable=False),
        ColumnSpec("cost_usd", "REAL", nullable=False),
        ColumnSpec("budget_scope", "TEXT", nullable=False, check="internal|pipeline"),
        ColumnSpec("router_impl", "TEXT", nullable=False, check="live|replay|scripted"),
        ColumnSpec("messages_sha256", "TEXT", nullable=True),
        ColumnSpec("fixture_key", "TEXT", nullable=True),
        ColumnSpec("created_at", "TEXT", nullable=False, note="ISO UTC"),
    ),
    indexes=(
        IndexSpec("ix_orch_llm_calls_task_created", ("task_id", "created_at")),
        IndexSpec("ix_orch_llm_calls_budget_scope", ("budget_scope",)),
    ),
)


ORCH_SCHEMA_MIGRATIONS = TableSpec(
    name="orch_schema_migrations",
    notes="Служебный журнал идемпотентного раннера WS3.",
    columns=(
        ColumnSpec("name", "TEXT", primary_key=True, nullable=False),
        ColumnSpec("applied_at", "TEXT", nullable=False, note="ISO UTC"),
    ),
)


TABLE_SPECS: tuple[TableSpec, ...] = (
    NODE_DEFS,
    NODE_PROMPT_VERSIONS,
    ORCH_REPORTS,
    ORCH_REPORT_VERSIONS,
    ORCH_TASKS,
    ORCH_STEPS,
    ORCH_CHECKPOINTS,
    ORCH_EVENTS,
    ORCH_EVENT_SEQ,
    ORCH_MESSAGES,
    ORCH_LLM_CALLS,
    ORCH_SCHEMA_MIGRATIONS,
)

TABLES_BY_NAME: dict[str, TableSpec] = {t.name: t for t in TABLE_SPECS}


WIRE_MODELS: dict[str, type[BaseModel]] = {
    "ActionResult": ActionResult,
    "ValidatorResult": ValidatorResult,
    "LLMUsage": LLMUsage,
    "LLMResponse": LLMResponse,
    "ChunkMetadata": ChunkMetadata,
    "Chunk": Chunk,
    "NodeDef": NodeDef,
    "ApprovalEvent": ApprovalEvent,
    "OrchEvent": OrchEvent,
    "ClassifyResult": ClassifyResult,
    "PlanStep": PlanStep,
    "SpendSnapshot": SpendSnapshot,
    "CheckpointData": CheckpointData,
}


def export_contract_schemas() -> dict[str, Any]:
    """JSON Schema для генерации TS (WS4, ``web/scripts/gen_orch_types.py``)."""
    return {
        "version": CONTRACT_VERSION,
        "changelog": [{"version": ver, "note": note} for ver, note in CONTRACT_CHANGELOG],
        "models": {name: model.model_json_schema() for name, model in WIRE_MODELS.items()},
        "tables": [spec.to_dict() for spec in TABLE_SPECS],
        "enums": {
            "TaskStatus": [s.value for s in TaskStatus],
            "MessageMode": [m.value for m in MessageMode],
            "CriticVerdict": [v.value for v in CriticVerdict],
            "NodeOrigin": [v.value for v in NodeOrigin],
            "NodeStatus": [v.value for v in NodeStatus],
            "NodeSource": [v.value for v in NodeSource],
            "PromptVersionKind": [v.value for v in PromptVersionKind],
            "PromptCreatedVia": [v.value for v in PromptCreatedVia],
            "ApprovalReason": [v.value for v in ApprovalReason],
            "ReportKind": [v.value for v in ReportKind],
            "AgentRole": [v.value for v in AgentRole],
            "ModelTier": [v.value for v in ModelTier],
            "LLMProvider": [v.value for v in LLMProvider],
            "LLMCallStatus": [v.value for v in LLMCallStatus],
            "BudgetScope": [v.value for v in BudgetScope],
            "RouterImpl": [v.value for v in RouterImpl],
            "ChunkCorpus": [v.value for v in ChunkCorpus],
            "Risk": [v.value for v in Risk],
            "EventType": list(EVENT_TYPES),
        },
        "fsm": {src.value: sorted(dst.value for dst in allowed) for src, allowed in FSM_TRANSITIONS.items()},
        "rules": {
            "run_node_actions": sorted(RUN_NODE_ACTIONS),
            "deletion_actions": sorted(DELETION_ACTIONS),
            "approval_risks": sorted(APPROVAL_RISKS),
            "pipeline_forecast_limit_usd": PIPELINE_FORECAST_LIMIT_USD,
            "validator_context_dry_run": VALIDATOR_CONTEXT_DRY_RUN,
            "risk_order": [item.value for item in Risk],
            "block_outcomes": {reason: status.value for reason, status in BLOCK_OUTCOMES.items()},
            "default_block_outcome": TaskStatus.failed.value,
        },
    }
