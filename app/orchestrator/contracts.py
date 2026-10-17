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
import re
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "ws3-r1.1"

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
    """Режим входящего сообщения (§3.3)."""

    interrupt = "interrupt"
    steer = "steer"
    append = "append"


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


class LLMResponse(BaseModel):
    """Ответ ``router.call`` (WS5). Текст промпта не дублируется в ``orch_llm_calls``."""

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


class ApprovalEvent(BaseModel):
    """Событие approval в SSE-шине.

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

    ``risk`` medium/high останавливает исполнителя на ``waiting_approval``
    (reason=risk) до выполнения шага. ``parallel_group`` — независимые
    read-only шаги с одним именем группы можно выполнять параллельно.
    ``estimated_cost`` — прогноз spend для критика, не фактическая оплата.
    """

    action: str
    params: dict[str, Any] = Field(default_factory=dict)
    expected: str = ""
    paid: bool = False
    mutating: bool = False
    risk: str = "none"
    parallel_group: str | None = None
    estimated_cost: float = 0.0


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


class ActionExecutor(Protocol):
    async def execute(self, name: str, params: dict[str, Any], task_ctx: dict[str, Any]) -> ActionResult:
        """WS2: ``actions.execute``."""


# Ключ context для dry-run. Согласовано с WS1: валидатор только читает.
VALIDATOR_CONTEXT_DRY_RUN = "dry_run"


class ValidatorRunner(Protocol):
    def run(self, name: str, project_id: int, context: dict[str, Any]) -> ValidatorResult:
        """WS1: синхронный ``validators.run(name, project_id, context) -> ValidatorResult``.

        Async тоже принимается: ``run_validator`` делает await, если ``run`` —
        корутина или вернул awaitable, иначе вызывает его через ``asyncio.to_thread``.

        ``context[VALIDATOR_CONTEXT_DRY_RUN] is True`` (ключ ``dry_run``) —
        валидатор только читает. Сигнатура и поля результата те же:
        ``name``, ``pass`` / ``passed``, ``details``, ``blocking``.
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
        передают ``budget_scope=internal``.

        До D6 tier хода — ``strong``, ``reasoning_effort=high``
        (``ORCH_DEFAULT_TIER`` / ``ORCH_REASONING_EFFORT``).

        ``on_delta(text)`` — боевой стрим WS5: каждый кусок текста. Нарезка
        готового ``response.text`` на слова — заглушка, не контракт UI.
        ``stream=True`` без ``on_delta`` эту заглушку не отменяет.
        """

    async def classify(self, message: str) -> ClassifyResult:
        """WS5: ``router.classify``. До D6 дефолт заглушки — tier ``strong``."""


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
    async def make_plan(self, goal: str, context: str = "") -> list[PlanStep]:
        """План через router + правила из ``rag.retrieve``."""


class Critic(Protocol):
    async def review(self, plan: list[PlanStep], context: str = "") -> str:
        """Вердикт approve | revise | reject."""


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
    }
)
APPROVAL_RISKS: frozenset[str] = frozenset({"medium", "high"})
PIPELINE_FORECAST_LIMIT_USD = 40.0


def is_deletion_action(action: str) -> bool:
    """Удаление кадра или сцены. Синтетический delete_frame обязан блокироваться."""
    name = (action or "").strip()
    if name in DELETION_ACTIONS:
        return True
    lowered = name.lower()
    return "delete_frame" in lowered or "delete_scene" in lowered


def plan_deletions(plan: list[PlanStep]) -> list[str]:
    return [step.action for step in plan if is_deletion_action(step.action)]


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


def forecast_cost(plan: list[PlanStep]) -> float:
    total = 0.0
    for step in plan:
        total += float(step.estimated_cost or 0)
        total += float(step.params.get("estimated_cost") or 0)
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
        ColumnSpec("mode", "TEXT", nullable=True, note="interrupt|steer|append после классификации"),
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
            "EventType": list(EVENT_TYPES),
        },
        "fsm": {src.value: sorted(dst.value for dst in allowed) for src, allowed in FSM_TRANSITIONS.items()},
        "rules": {
            "run_node_actions": sorted(RUN_NODE_ACTIONS),
            "deletion_actions": sorted(DELETION_ACTIONS),
            "approval_risks": sorted(APPROVAL_RISKS),
            "pipeline_forecast_limit_usd": PIPELINE_FORECAST_LIMIT_USD,
        },
    }
