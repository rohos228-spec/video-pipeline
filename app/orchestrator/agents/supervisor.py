"""Супервизор: единственная точка записи статусов FSM.

R3: planner → dry-run валидаторов → critic (≤2 revise, затем
waiting_approval reason=critic_escalation) → executor только через
actions.execute. Read-only одной parallel_group идут параллельно,
мутации — последовательно. Чекпоинт после каждой мутации. Approval —
по max(риск реестра, подсказка плана): план не понижает риск.
Paid-retry guard не даёт
повторно оплатить тот же шаг. Interrupt отменяет неплатный ход,
платный ждёт границы. Kill switch запрещает новые действия и взводит
стоп-ключ, не разрывая платный шаг mid-flight.

Очередь ``MessageMode.task``: вторая задача не стартует, пока в сессии
есть нетерминальная, включая ``paused`` и ``waiting_approval``.
``resume`` и ``grant_approval`` продолжают ту же задачу. Дренаж —
только из терминального статуса. После рестарта ``recover_after_restart``
снова поднимает строки ``queued`` и продолжает оборванное выполнение
с последнего чекпоинта. Платный шаг со строкой ``running`` (исход
неизвестен) сам не исполняется снова: ``waiting_approval`` /
``paid_outcome_unknown``. «Шаг выполнен, продолжить» пишет шаг ``done``
и в ``_paid_done``. «Перезапустить шаг» сначала отдаёт прогноз, потом
одноразовый grant. «Остановить задачу» ставит шаг и задачу в ``failed``.

Dry-run валидаторов (``dry_run=True``) идёт до критика и только читает.
Live — до и после мутации и может остановить задачу. ``project_id``
берётся из строки задачи. Нуля нет. Без id read-only план идёт как раньше,
а платный или мутирующий (``paid``, ``mutating`` или risk = max(реестр, план)
из ``APPROVAL_RISKS``) уходит в ``waiting_approval`` с ``missing_project_id``.

Исход блокировок — ``block_status`` / ``BLOCK_OUTCOMES``. ``deletion_blocked``
терминальный: grant его не исполняет. После grant, до ``_execute_plan``,
план снова проходит ``_screen_plan``. Пустой план чекпоинта на grant/resume —
намеренно ``failed`` с причиной ``empty_plan``, без replan: исполнять нечего.
``missing_project_id`` после grant снова ждёт: id задним числом не пишется.

Грант риска — ключ (задача, индекс, действие, публичные params) и сбрасывается
при ``save_plan`` / steer. Бюджет проверяется на каждой итерации плана и перед
платным шагом. ``ok=False`` пишет шаг ``failed`` и не кладёт его в paid-retry.
Read-only и удаление смотрит реестр; флаги плана только ужесточают.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from app.orchestrator.agents.executor import effective_risk, run_many
from app.orchestrator.agents.stubs import (
    ActionBackedExecutor,
    StubCritic,
    StubPlanner,
    UnlinkedActions,
    UnlinkedValidators,
)
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.agents.validator_call import run_validator
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import (
    APPROVAL_RISKS,
    ORCH_REASONING_EFFORT,
    TERMINAL_STATUSES,
    VALIDATOR_CONTEXT_DRY_RUN,
    AgentRole,
    ApprovalDecision,
    ApprovalEvent,
    ApprovalReason,
    BudgetScope,
    CheckpointData,
    PlanStep,
    SpendSnapshot,
    TaskStatus,
    ValidatorResult,
    approval_decision_choices,
    block_status,
    budget_exhausted_response,
    can_transition,
    ceiling_blocks,
    forecast_cost,
    forecast_unknown,
    grant_forbidden,
    orch_run_id,
    paid_step_identity,
    plan_missing_verify,
    plan_without_client_money,
    server_forecast_limit,
    step_is_deletion,
    step_is_readonly,
    usable_project_id,
    validator_budget_exhausted,
    validator_missing_run_id,
)
from app.orchestrator.events import EventBus

_DRY_RUN = ("plan_sanity", "no_deletions", "spend_guard")
_RESUME_STATUSES = frozenset({TaskStatus.planning, TaskStatus.critic_review, TaskStatus.executing})
_HOLD_STATUSES = frozenset({TaskStatus.paused, TaskStatus.waiting_approval})


class InvalidTransition(RuntimeError):
    pass


def _coerce_decision(decision: ApprovalDecision | str | None) -> ApprovalDecision | None:
    if decision is None or decision == "":
        return None
    if isinstance(decision, ApprovalDecision):
        return decision
    return ApprovalDecision(str(decision))


class Supervisor:
    def __init__(
        self,
        bus: EventBus,
        checkpoints: CheckpointStore,
        tasks: TaskStore | None = None,
        actions: Any | None = None,
        validators: Any | None = None,
        planner: Any | None = None,
        critic: Any | None = None,
        executor: Any | None = None,
    ) -> None:
        self.bus = bus
        self.checkpoints = checkpoints
        self.tasks = tasks or TaskStore()
        self.actions = actions or UnlinkedActions()
        self.validators = validators or UnlinkedValidators()
        self.planner = planner or StubPlanner()
        self.critic = critic or StubCritic()
        self.executor = executor or ActionBackedExecutor(self.actions)
        self.history: list[tuple[int, str]] = []
        self._runs: dict[int, asyncio.Task[Any]] = {}
        self._unpaid: dict[str, asyncio.Task[Any]] = {}
        self._cancel: set[str] = set()
        self._pause: set[str] = set()
        self._kill: set[str] = set()
        self._steer: dict[str, str] = {}
        self._approval: dict[str, str] = {}
        self._session_task: dict[str, int] = {}
        self._change_ids: dict[int, list[str]] = {}
        self._context: dict[int, str] = {}
        self._risk_granted: set[tuple[int, int, str]] = set()
        self._paid_done: dict[int, set[str]] = {}
        self._retry_armed: dict[int, str] = {}
        self._retry_spent: set[tuple[int, str]] = set()
        self._queued: dict[str, list[tuple[int, str]]] = {}
        self._budget_stop: set[int] = set()
        self._shield_orphaned: set[int] = set()
        self._paid_open: set[int] = set()
        self._paid_outcome_hold: set[int] = set()
        self._pending_paid_unknown: list[tuple[str, int]] = []
        self._step_recorded: set[int] = set()
        self._shield_jobs: set[asyncio.Task[Any]] = set()
        self._block_reason: dict[int, str] = {}
        self._pending_kill_publish: list[tuple[str, int | None]] = []
        self._pending_queue_cancel: list[tuple[str, int]] = []
        self._approved: dict[int, str] = {}

    async def start_task(
        self,
        session_id: str,
        goal: str,
        project_id: int | None = None,
    ) -> int:
        self._cancel.discard(session_id)
        self._pause.discard(session_id)
        self._kill.discard(session_id)
        self._steer.pop(session_id, None)
        self._approval.pop(session_id, None)
        task_id = await self._set_status(
            None,
            TaskStatus.queued,
            session_id=session_id,
            goal=goal,
            project_id=usable_project_id(project_id),
        )
        self._session_task[session_id] = task_id
        self._change_ids[task_id] = []
        self._context[task_id] = ""
        self._paid_done[task_id] = set()
        self._runs[task_id] = asyncio.create_task(
            self._drive(task_id, session_id, goal),
            name=f"orch-task-{task_id}",
        )
        return task_id

    async def recover_after_restart(self) -> list[int]:
        """Продолжить задачи, которые остались ``executing`` после падения процесса.

        Платная строка ``running`` не исполняется снова: ``waiting_approval``
        с причиной ``paid_unknown_outcome``.
        """
        rows = self.tasks.list_open()
        recovered: list[int] = []
        for row in rows:
            if str(row.get("status")) != TaskStatus.executing.value:
                continue
            task_id = int(row["id"])
            if task_id in self._runs and not self._runs[task_id].done():
                continue
            session_id = str(row["session_id"])
            data = self.checkpoints.load_latest(task_id)
            if data is None or not data.plan:
                self._session_task[session_id] = task_id
                await self._fail_closed(task_id, session_id, "empty_plan")
                recovered.append(task_id)
                continue
            self._arm_resume(session_id, task_id, data)
            self._session_task[session_id] = task_id
            self._runs[task_id] = asyncio.create_task(
                self._execute_plan(
                    task_id,
                    session_id,
                    list(data.plan),
                    int(data.position),
                    str(row.get("goal") or ""),
                ),
                name=f"orch-recover-{task_id}",
            )
            recovered.append(task_id)
        return recovered

    async def wait(self, task_id: int, timeout: float | None = None) -> None:
        await asyncio.wait_for(self._runs[task_id], timeout)

    async def resume(self, task_id: int) -> None:
        row = self.tasks.get(task_id)
        if row is None:
            raise LookupError(task_id)
        data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        if data is None:
            raise LookupError(f"нет чекпоинта для задачи {task_id}")
        session_id = str(row["session_id"])
        self._arm_resume(session_id, task_id, data)
        if self._status(task_id) is not TaskStatus.executing:
            await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        await self._flush_kill_publishes()
        if not data.plan:
            await self._apply_block(task_id, session_id, "empty_plan")
            return
        self._runs[task_id] = asyncio.create_task(
            self._execute_plan(
                task_id,
                session_id,
                list(data.plan),
                int(data.position),
                str(row.get("goal") or ""),
            ),
            name=f"orch-resume-{task_id}",
        )

    async def grant_approval(
        self,
        session_id: str,
        decision: ApprovalDecision | str | None = None,
        *,
        retry_grant: bool = False,
    ) -> dict[str, Any] | None:
        """Продолжить с чекпоинта после waiting_approval.

        Терминальная причина из ``BLOCK_OUTCOMES`` не исполняется.
        Перед исполнением план снова проходит ``_screen_plan``. Пустой план
        чекпоинта — намеренно ``failed`` / ``empty_plan``, без replan.
        ``missing_project_id`` не терминальный, но экран снова остановит
        задачу: записать ``project_id`` после старта нечем.
        Грант риска покрывает только шаг чекпоинта и только это действие
        с этими публичными params.

        ``decision`` пустой — прежний путь. Для ``paid_outcome_unknown``
        пустой decision по-прежнему не вызывает ``execute``. Явные значения:
        ``approve`` помечает шаг сделанным и идёт дальше, ``retry_step``
        только отдаёт прогноз, ``deny`` проваливает шаг и задачу.
        ``retry_grant`` — отдельный одноразовый перезапуск после прогноза.
        """
        task_id = self._session_task.get(session_id)
        if task_id is None:
            raise LookupError(session_id)
        if self._status(task_id) is not TaskStatus.waiting_approval:
            raise InvalidTransition("grant_approval только из waiting_approval")
        if grant_forbidden(self._block_reason.get(task_id)):
            raise InvalidTransition("grant запрещён: терминальный отказ")
        decided = _coerce_decision(decision)
        reason = self._block_reason.get(task_id)
        if retry_grant:
            return await self._consume_paid_retry_grant(task_id, session_id)
        if reason == "paid_outcome_unknown" and decided is not None:
            if decided is ApprovalDecision.approve:
                await self._approve_paid_unknown(task_id, session_id)
                return {"executed": False, "decision": ApprovalDecision.approve.value}
            if decided is ApprovalDecision.deny:
                await self._deny_paid_unknown(task_id, session_id)
                return {"executed": False, "decision": ApprovalDecision.deny.value}
            if decided is ApprovalDecision.retry_step:
                return await self._arm_paid_retry_forecast(task_id, session_id)
            raise InvalidTransition(f"неизвестное decision {decided.value}")
        if decided in {ApprovalDecision.retry_step, ApprovalDecision.deny}:
            raise InvalidTransition("retry_step и deny только для paid_outcome_unknown")
        data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        self._budget_stop.discard(task_id)
        if data is None:
            await self._replan_from_grant(task_id, session_id)
            return
        self._arm_resume(session_id, task_id, data)
        await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        await self._flush_kill_publishes()
        plan = list(data.plan)
        if not plan:
            await self._apply_block(task_id, session_id, "empty_plan")
            return
        if await self._screen_plan(task_id, session_id, plan):
            return
        if not self._plan_was_approved(task_id, plan):
            await self._replan_from_grant(task_id, session_id)
            return
        position = int(data.position)
        if position < len(plan):
            self._risk_granted.add(self._risk_grant_key(task_id, position, plan[position]))
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        self._runs[task_id] = asyncio.create_task(
            self._execute_plan(task_id, session_id, plan, position, goal),
            name=f"orch-approve-{task_id}",
        )

    async def _replan_from_grant(self, task_id: int, session_id: str) -> None:
        """Эскалация случилась до approve: цикл planner → critic заново, с позиции 0."""
        self._approved.pop(task_id, None)
        self._cancel.discard(session_id)
        self._pause.discard(session_id)
        self._steer.pop(session_id, None)
        self._approval.pop(session_id, None)
        self._session_task[session_id] = task_id
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        self._runs[task_id] = asyncio.create_task(
            self._drive(task_id, session_id, goal),
            name=f"orch-replan-{task_id}",
        )

    def _plan_was_approved(self, task_id: int, plan: list[PlanStep]) -> bool:
        mark = self._approved.get(task_id)
        return mark is not None and mark == _plan_fingerprint(plan)

    def _arm_resume(self, session_id: str, task_id: int, data: CheckpointData) -> None:
        self._cancel.discard(session_id)
        self._pause.discard(session_id)
        self._steer.pop(session_id, None)
        self._approval.pop(session_id, None)
        self._session_task[session_id] = task_id
        self._change_ids[task_id] = list(data.change_ids)
        self._context[task_id] = data.context
        self._remember_paid(task_id)

    def request_cancel(self, session_id: str) -> None:
        """Взвести cancel-флаг и отменить текущий неплатный шаг, если он есть.

        Платный шаг уже в полёте не откатывается: задача уходит в
        ``waiting_approval`` с причиной ``paid_outcome_unknown``.
        """
        self._cancel.add(session_id)
        task_id = self._session_task.get(session_id)
        if task_id is not None:
            self.tasks.set_flag(task_id, cancel_requested=1)
        if task_id is not None and task_id in self._paid_open:
            self._paid_outcome_hold.add(task_id)
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                self._pending_paid_unknown.append((session_id, task_id))
                return
            self._spawn(self._mark_paid_outcome_unknown(session_id, task_id))
            return
        inflight = self._unpaid.get(session_id)
        if inflight is not None and not inflight.done():
            inflight.cancel()

    def request_kill(self, session_id: str) -> asyncio.Task[Any] | None:
        """Kill switch: неплатное — сразу, новые действия запрещены.

        Платный шаг уже в полёте не откатывается: задача уходит в
        ``waiting_approval`` с причиной ``paid_outcome_unknown``.
        Стоп-ключ узла здесь не ставится. Событие несёт ``stop_key_set=false``
        и ``stop_key=pending``. Возвращает задачу публикации, если цикл уже
        есть: очередь её дожидается, иначе запись sqlite могла бы отстать
        от ``drain``. Без цикла событие остаётся в очереди до resume/grant.
        """
        self._kill.add(session_id)
        self.request_cancel(session_id)
        queued = list(self._queued.pop(session_id, []))
        task_id = self._session_task.get(session_id)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("kill_switch for {} queued: no running loop", session_id)
            self._pending_kill_publish.append((session_id, task_id))
            self._pending_queue_cancel.extend((session_id, queued_id) for queued_id, _goal in queued)
            return None
        for queued_id, _goal in queued:
            self._spawn(self._cancel_queued(session_id, queued_id))
        return self._spawn(self._publish_kill(session_id, task_id))

    async def _cancel_queued(self, session_id: str, task_id: int) -> None:
        """Задача, стоявшая в очереди на момент kill, не должна стартовать позже."""
        if self._status(task_id) is TaskStatus.queued:
            await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)

    def request_pause(self, session_id: str) -> None:
        self._pause.add(session_id)
        task_id = self._session_task.get(session_id)
        if task_id is not None:
            self.tasks.set_flag(task_id, pause_requested=1)

    def request_steer(self, session_id: str, text: str) -> None:
        self._steer[session_id] = text
        task_id = self._session_task.get(session_id)
        if task_id is not None:
            self.tasks.set_flag(task_id, steer_text=text)

    def request_approval(self, session_id: str, reason: str) -> None:
        """Попросить человека. Статус сменится на границе шага, не mid-flight.

        Для исчерпания суб-бюджета reason='budget_exhausted': FSM остаётся
        waiting_approval, отдельный статус не заводится.
        """
        self._approval[session_id] = reason

    def active_task(self, session_id: str) -> int | None:
        task_id = self._session_task.get(session_id)
        if task_id is None:
            return None
        status = self._status(task_id)
        if status is None or status in TERMINAL_STATUSES:
            return None
        return task_id

    async def submit_task(
        self,
        session_id: str,
        goal: str,
        project_id: int | None = None,
    ) -> int:
        """Новая задача (``MessageMode.task``). Активную не меняет: встаёт в очередь.

        Активная — любая нетерминальная, в том числе ``paused`` и
        ``waiting_approval``. Рядом с ней вторая не стартует.
        """
        active = self.active_task(session_id)
        bound = usable_project_id(project_id)
        if active is not None:
            task_id = self.tasks.insert(session_id, goal, TaskStatus.queued.value, project_id=bound)
            self._queued.setdefault(session_id, []).append((task_id, goal))
            return task_id
        return await self.start_task(session_id, goal, project_id=bound)

    async def recover_after_restart(self) -> list[int]:
        """Поднять незавершённые задачи после рестарта процесса.

        ``queued`` возвращаются в память очереди. Если сессия свободна,
        стартует самая старая. ``planning`` / ``critic_review`` / ``executing``
        продолжаются с последнего чекпоинта. ``paused`` и ``waiting_approval``
        сами не стартуют и не отпускают очередь сессии.

        Платный шаг с ``orch_steps.status=running`` доходит до ``_execute_plan``
        и там останавливается: ``waiting_approval`` / ``paid_outcome_unknown``.
        ``execute`` не вызывается, пока человек не выберет кнопку.
        """
        rows = await asyncio.to_thread(self.tasks.list_open)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["session_id"]), []).append(row)
        started: list[int] = []
        for session_id, group in grouped.items():
            started.extend(await self._recover_session(session_id, group))
        return started

    async def _recover_session(self, session_id: str, rows: list[dict[str, Any]]) -> list[int]:
        tracked = self._tracked_ids(session_id)
        fresh = [row for row in rows if int(row["id"]) not in tracked]
        if not fresh:
            return []
        queued = [row for row in fresh if str(row["status"]) == TaskStatus.queued.value]
        held = [row for row in fresh if TaskStatus(str(row["status"])) in _HOLD_STATUSES]
        interrupted = [row for row in fresh if TaskStatus(str(row["status"])) in _RESUME_STATUSES]
        queued_pairs = [(int(row["id"]), str(row.get("goal") or "")) for row in queued]
        if interrupted:
            primary = interrupted[0]
            task_id = int(primary["id"])
            self._queued[session_id] = self._merge_queued(session_id, queued_pairs)
            if await self._spawn_from_checkpoint(task_id, primary):
                return [task_id]
            self._session_task[session_id] = task_id
            return []
        if held:
            self._session_task[session_id] = int(held[0]["id"])
            self._queued[session_id] = self._merge_queued(session_id, queued_pairs)
            return []
        if not queued:
            return []
        first, *rest = queued
        rest_pairs = [(int(row["id"]), str(row.get("goal") or "")) for row in rest]
        self._queued[session_id] = self._merge_queued(session_id, rest_pairs)
        task_id = int(first["id"])
        self._spawn_drive(task_id, session_id, str(first.get("goal") or ""))
        return [task_id]

    async def _spawn_from_checkpoint(self, task_id: int, row: dict[str, Any]) -> bool:
        running = self._runs.get(task_id)
        if running is not None and not running.done():
            return True
        data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        if data is None:
            data = self._checkpoint_from_row(row)
        if data is None:
            logger.warning(
                "orch recover {}: нет чекпоинта, статус {} оставлен",
                task_id,
                row.get("status"),
            )
            return False
        session_id = str(row["session_id"])
        self._arm_resume(session_id, task_id, data)
        self._runs[task_id] = asyncio.create_task(
            self._continue_after_restart(task_id, session_id, data, str(row.get("goal") or "")),
            name=f"orch-recover-{task_id}",
        )
        return True

    async def _continue_after_restart(
        self,
        task_id: int,
        session_id: str,
        data: CheckpointData,
        goal: str,
    ) -> None:
        current = self._status(task_id)
        if current is TaskStatus.planning:
            await self._set_status(task_id, TaskStatus.critic_review, session_id=session_id)
            current = self._status(task_id)
        if current is TaskStatus.critic_review:
            await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
            current = self._status(task_id)
        if current is not TaskStatus.executing:
            return
        if not data.plan:
            await self._apply_block(task_id, session_id, "empty_plan")
            return
        await self._execute_plan(task_id, session_id, list(data.plan), int(data.position), goal)

    def _spawn_drive(self, task_id: int, session_id: str, goal: str) -> None:
        self._cancel.discard(session_id)
        self._pause.discard(session_id)
        self._kill.discard(session_id)
        self._steer.pop(session_id, None)
        self._approval.pop(session_id, None)
        self._session_task[session_id] = task_id
        self._change_ids.setdefault(task_id, [])
        self._context.setdefault(task_id, "")
        self._paid_done.setdefault(task_id, set())
        self._runs[task_id] = asyncio.create_task(
            self._drive(task_id, session_id, goal),
            name=f"orch-recover-{task_id}",
        )

    def _checkpoint_from_row(self, row: dict[str, Any]) -> CheckpointData | None:
        raw = row.get("plan_json")
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, list) or not payload:
            return None
        return CheckpointData(
            plan=[PlanStep.model_validate(item) for item in payload],
            position=int(row.get("position") or 0),
            spend=SpendSnapshot(
                internal=float(row.get("spend_internal") or 0),
                actions=float(row.get("spend_actions") or 0),
            ),
        )

    def _tracked_ids(self, session_id: str) -> set[int]:
        tracked = {task_id for task_id, _goal in self._queued.get(session_id, [])}
        tracked.update(task_id for task_id, job in self._runs.items() if not job.done())
        active = self._session_task.get(session_id)
        if active is not None:
            tracked.add(active)
        return tracked

    def _merge_queued(
        self,
        session_id: str,
        pairs: list[tuple[int, str]],
    ) -> list[tuple[int, str]]:
        merged = list(self._queued.get(session_id, []))
        seen = {task_id for task_id, _goal in merged}
        for task_id, goal in pairs:
            if task_id in seen or task_id in self._runs:
                continue
            merged.append((task_id, goal))
            seen.add(task_id)
        return merged

    def _paid_outcome_unknown(self, task_id: int, step: PlanStep) -> bool:
        """Платная строка ``running``: шаг уже стартовал, результат не записан."""
        return self._running_paid_row(task_id, step) is not None

    def _running_paid_row(self, task_id: int, step: PlanStep) -> dict[str, Any] | None:
        if not step.paid:
            return None
        ident = _public_params(step.params)
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) != "running" or not row.get("paid"):
                continue
            if str(row.get("action_name") or "") != step.action:
                continue
            try:
                stored = json.loads(row.get("params_json") or "{}")
            except json.JSONDecodeError:
                stored = {}
            if isinstance(stored, dict) and _public_params(stored) == ident:
                return row
        return None

    def _retry_grant_key(self, step: PlanStep) -> str:
        """Действие + публичные params + признак retry. Одноразовый ключ grant."""
        return paid_step_identity(step.action, step.params) + "\nretry"

    async def _checkpoint_step(self, task_id: int) -> tuple[CheckpointData, PlanStep]:
        data = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
        if data is None:
            raise LookupError(f"нет чекпоинта для задачи {task_id}")
        if data.position < 0 or data.position >= len(data.plan):
            raise LookupError(f"нет шага чекпоинта для задачи {task_id}")
        return data, data.plan[int(data.position)]

    async def _continue_from_checkpoint(self, task_id: int, session_id: str, data: CheckpointData) -> None:
        self._arm_resume(session_id, task_id, data)
        await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        self._runs[task_id] = asyncio.create_task(
            self._execute_plan(task_id, session_id, list(data.plan), int(data.position), goal),
            name=f"orch-paid-unknown-{task_id}",
        )

    async def _approve_paid_unknown(self, task_id: int, session_id: str) -> None:
        """«Шаг выполнен, продолжить»: done и _paid_done, execute этого шага нет."""
        data, step = await self._checkpoint_step(task_id)
        row = self._running_paid_row(task_id, step)
        if row is not None:
            self.tasks.finish_step(int(row["id"]), "done", None, 0.0)
        self._paid_done.setdefault(task_id, set()).add(paid_step_identity(step.action, step.params))
        self._block_reason.pop(task_id, None)
        await self._continue_from_checkpoint(task_id, session_id, data)

    async def _deny_paid_unknown(self, task_id: int, session_id: str) -> None:
        """«Остановить задачу»: шаг и задача failed, execute не вызывается."""
        _data, step = await self._checkpoint_step(task_id)
        row = self._running_paid_row(task_id, step)
        if row is not None:
            self.tasks.finish_step(int(row["id"]), "failed", None, 0.0)
        self._block_reason.pop(task_id, None)
        await self._set_status(task_id, TaskStatus.failed, session_id=session_id, error="paid_outcome_unknown")
        await self.bus.publish(
            session_id,
            "task_failed",
            {"error": "paid_outcome_unknown", "action": step.action},
            task_id=task_id,
        )

    async def _arm_paid_retry_forecast(self, task_id: int, session_id: str) -> dict[str, Any]:
        """«Перезапустить шаг»: прогноз, без execute. Grant вооружается отдельно."""
        data, step = await self._checkpoint_step(task_id)
        key = self._retry_grant_key(step)
        if (task_id, key) not in self._retry_spent:
            self._retry_armed[task_id] = key
        forecast = forecast_cost([step])
        await self.bus.publish(
            session_id,
            "approval_required",
            {
                "reason": "paid_outcome_unknown",
                "task_id": task_id,
                "action": step.action,
                "position": int(data.position),
                "forecast_usd": forecast,
                "decisions": approval_decision_choices(),
                "retry_armed": self._retry_armed.get(task_id) == key,
            },
            task_id=task_id,
        )
        return {
            "executed": False,
            "decision": ApprovalDecision.retry_step.value,
            "forecast_usd": forecast,
        }

    async def _consume_paid_retry_grant(self, task_id: int, session_id: str) -> dict[str, Any]:
        """Одноразовый retry grant. Без прогноза и повторно execute не зовётся.

        Пока задача в ``_budget_stop``, ключ не сгорает и шаг не стартует.
        """
        if self._retry_armed.get(task_id) is None and any(stored == task_id for stored, _key in self._retry_spent):
            return {"executed": False, "reason": "retry_grant_spent", "forecast_usd": None}
        if task_id not in self._retry_armed:
            return {"executed": False, "reason": "retry_grant_required", "forecast_usd": None}
        data, step = await self._checkpoint_step(task_id)
        key = self._retry_grant_key(step)
        forecast = forecast_cost([step])
        if (task_id, key) in self._retry_spent or self._retry_armed.get(task_id) != key:
            reason = "retry_grant_spent" if (task_id, key) in self._retry_spent else "retry_grant_required"
            return {"executed": False, "reason": reason, "forecast_usd": forecast}
        if task_id in self._budget_stop:
            await self.bus.publish(
                session_id,
                "approval_required",
                {
                    "reason": ApprovalReason.budget_exhausted.value,
                    "task_id": task_id,
                    "action": step.action,
                    "forecast_usd": forecast,
                },
                task_id=task_id,
            )
            return {
                "executed": False,
                "reason": ApprovalReason.budget_exhausted.value,
                "forecast_usd": forecast,
            }
        self._retry_spent.add((task_id, key))
        self._retry_armed.pop(task_id, None)
        row = self._running_paid_row(task_id, step)
        if row is not None:
            self.tasks.finish_step(int(row["id"]), "cancelled", None, 0.0)
        self._block_reason.pop(task_id, None)
        await self._continue_from_checkpoint(task_id, session_id, data)
        return {"executed": True, "decision": ApprovalDecision.retry_step.value, "forecast_usd": forecast}

    def _llm_kwargs(self, task_id: int, role: str) -> dict[str, Any]:
        return {
            "run_id": orch_run_id(task_id=task_id),
            "task_id": task_id,
            "agent_role": role,
            "budget_scope": BudgetScope.internal.value,
            "reasoning_effort": ORCH_REASONING_EFFORT,
        }

    def _spawn(self, awaitable: Any) -> asyncio.Task[Any]:
        job: asyncio.Task[Any] = asyncio.get_running_loop().create_task(awaitable)
        self._shield_jobs.add(job)
        job.add_done_callback(self._shield_jobs.discard)
        return job

    async def _flush_kill_publishes(self) -> None:
        pending = list(self._pending_kill_publish)
        self._pending_kill_publish.clear()
        for session_id, task_id in pending:
            self._spawn(self._publish_kill(session_id, task_id))
        queued = list(self._pending_queue_cancel)
        self._pending_queue_cancel.clear()
        for session_id, task_id in queued:
            self._spawn(self._cancel_queued(session_id, task_id))
        pending_paid = list(self._pending_paid_unknown)
        self._pending_paid_unknown.clear()
        for session_id, task_id in pending_paid:
            self._spawn(self._mark_paid_outcome_unknown(session_id, task_id))

    async def _publish_kill(self, session_id: str, task_id: int | None) -> None:
        await self.bus.publish(
            session_id,
            "kill_switch",
            {"stop_key_set": False, "stop_key": "pending", "only_node": True, "task_id": task_id},
            task_id=task_id,
        )

    async def _drive(self, task_id: int, session_id: str, goal: str) -> None:
        try:
            plan = await self._plan_and_review(task_id, session_id, goal, self._context.get(task_id, ""))
            if plan is None:
                return
            await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
            await self._execute_plan(task_id, session_id, plan, 0, goal)
        except asyncio.CancelledError:
            if task_id in self._paid_open or task_id in self._shield_orphaned:
                if asyncio.current_task() and asyncio.current_task().cancelling():
                    raise
                return
            current = self._status(task_id)
            if current not in TERMINAL_STATUSES:
                await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
            if asyncio.current_task() and asyncio.current_task().cancelling():
                raise
        except Exception:
            logger.exception("orch task {} failed", task_id)
            current = self._status(task_id)
            if current not in TERMINAL_STATUSES:
                await self._set_status(
                    task_id,
                    TaskStatus.failed,
                    session_id=session_id,
                    error="drive_failed",
                )
                await self.bus.publish(
                    session_id,
                    "task_failed",
                    {"error": "drive_failed"},
                    task_id=task_id,
                )

    async def _plan_and_review(
        self,
        task_id: int,
        session_id: str,
        goal: str,
        context: str,
    ) -> list[PlanStep] | None:
        plan = await self._make_plan(task_id, session_id, goal, context)
        if plan is None:
            return None
        revises = 0
        while True:
            blocked = await self._screen_plan(task_id, session_id, plan)
            if blocked:
                return None
            await self._set_status(task_id, TaskStatus.critic_review, session_id=session_id)
            verdict = await self._review_plan(task_id, session_id, plan, context)
            if verdict is None:
                return None
            if verdict == "approve":
                self._approved[task_id] = _plan_fingerprint(plan)
                return plan
            if verdict == "reject":
                await self._checkpoint(task_id, session_id, plan, 0)
                await self._apply_block(task_id, session_id, "critic_reject")
                return None
            revises += 1
            if revises > 2:
                await self._apply_block(task_id, session_id, ApprovalReason.critic_escalation.value)
                return None
            context = (context + "\n" + f"critic: {verdict}").strip()
            self._context[task_id] = context
            plan = await self._make_plan(task_id, session_id, goal, context)
            if plan is None:
                return None

    async def _make_plan(
        self,
        task_id: int,
        session_id: str,
        goal: str,
        context: str,
    ) -> list[PlanStep] | None:
        if task_id in self._budget_stop:
            await self._settle_stopped(task_id, session_id)
            return None
        await self._set_status(task_id, TaskStatus.planning, session_id=session_id)
        plan = list(
            await self._cancellable(
                session_id,
                self.planner.make_plan(
                    goal,
                    context=context,
                    **self._llm_kwargs(task_id, AgentRole.planner.value),
                ),
            )
        )
        self._save_plan(task_id, plan)
        if await self._absorb_budget(task_id, session_id, getattr(self.planner, "last_response", None)):
            return None
        return plan

    async def _review_plan(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        context: str,
    ) -> str | None:
        if task_id in self._budget_stop:
            await self._settle_stopped(task_id, session_id)
            return None
        verdict = str(
            await self._cancellable(
                session_id,
                self.critic.review(
                    plan,
                    context=context,
                    **self._llm_kwargs(task_id, AgentRole.critic.value),
                ),
            )
        )
        if await self._absorb_budget(task_id, session_id, getattr(self.critic, "last_response", None)):
            return None
        return verdict

    async def absorb_budget(
        self,
        task_id: int,
        session_id: str,
        response: Any,
        position: int | None = None,
    ) -> bool:
        """Публичная остановка по суб-бюджету. На терминальной задаче — no-op.

        ``InvalidTransition`` не выходит наружу: очередь не должна
        принимать её за сбой классификатора.
        """
        status = self._status(task_id)
        if status is None or status in TERMINAL_STATUSES:
            return False
        if status is TaskStatus.waiting_approval and task_id in self._budget_stop:
            return True
        try:
            return await self._absorb_budget(task_id, session_id, response, position=position)
        except InvalidTransition:
            logger.warning("absorb_budget skipped for task {} in {}", task_id, status.value)
            return False

    async def _absorb_budget(
        self,
        task_id: int,
        session_id: str,
        response: Any,
        position: int | None = None,
    ) -> bool:
        """Чекпоинт и waiting_approval, если роутер сообщил исчерпание суб-бюджета."""
        if not budget_exhausted_response(response):
            return False
        if task_id not in self._budget_stop:
            self._budget_stop.add(task_id)
            text = str(getattr(response, "text", "") or "")
            self._context[task_id] = (self._context.get(task_id, "") + "\n" + text).strip()
            plan = self._stored_plan(task_id)
            existing = await asyncio.to_thread(self.checkpoints.load_latest, task_id)
            if existing is not None and not plan:
                plan = list(existing.plan)
            if (
                existing is not None
                and plan
                and _plan_fingerprint(list(existing.plan)) != _plan_fingerprint(plan)
            ):
                position = 0
            elif position is None:
                position = int(existing.position) if existing is not None else 0
            await self.bus.publish(
                session_id,
                "message",
                {"role": "assistant", "text": text, "kept": True},
                task_id=task_id,
            )
            await self._checkpoint(task_id, session_id, plan, position)
            approval = getattr(response, "approval", None)
            extra: dict[str, Any] | None = None
            if approval is not None and hasattr(approval, "model_dump"):
                extra = approval.model_dump()
                extra.pop("reason", None)
            await self._apply_block(
                task_id,
                session_id,
                ApprovalReason.budget_exhausted.value,
                extra=extra,
            )
        return True

    def _stored_plan(self, task_id: int) -> list[PlanStep]:
        row = self.tasks.get(task_id) or {}
        raw = row.get("plan_json")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        return [PlanStep.model_validate(item) for item in data]

    async def _screen_plan(self, task_id: int, session_id: str, plan: list[PlanStep]) -> bool:
        """Локальные запреты, project_id и dry-run. True — план отклонён.

        Пустой план на этом пути — ``plan_sanity`` (планёр ничего не вернул).
        Пустой чекпоинт на grant/resume сюда не попадает: это ``empty_plan``.
        """
        if not plan:
            await self._apply_block(task_id, session_id, "plan_sanity")
            return True
        if ceiling_blocks(server_forecast_limit()) or forecast_unknown(plan):
            await self._apply_block(task_id, session_id, "unknown_cost")
            return True
        for step in plan:
            if step_is_deletion(step, self.actions):
                await self._apply_block(task_id, session_id, "deletion_blocked")
                return True
        if plan_missing_verify(plan):
            await self._checkpoint(task_id, session_id, plan, 0)
            await self._apply_block(task_id, session_id, "plan_sanity")
            return True
        if self._task_project_id(task_id) is None and _plan_requires_project(plan, self.actions):
            await self._apply_block(task_id, session_id, ApprovalReason.missing_project_id.value)
            return True
        dry = await self._dry_run(task_id, session_id, plan)
        if dry in {"budget", "missing_run", "project"}:
            return True
        if dry == "blocked":
            await self._apply_block(task_id, session_id, "validator_blocked")
            return True
        return False

    def _forget_risk_grants(self, task_id: int) -> None:
        self._risk_granted = {key for key in self._risk_granted if key[0] != task_id}

    def _risk_grant_key(self, task_id: int, index: int, step: PlanStep) -> tuple[int, int, str]:
        return (task_id, index, paid_step_identity(step.action, step.params))

    def _save_plan(self, task_id: int, plan: list[PlanStep], position: int | None = None) -> None:
        """Новый план сбрасывает гранты риска и отметку approve этой задачи."""
        self._forget_risk_grants(task_id)
        self._approved.pop(task_id, None)
        if position is None:
            self.tasks.save_plan(task_id, plan)
            return
        self.tasks.save_plan(task_id, plan, position=position)

    async def _stop_if_budget(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        index: int,
    ) -> bool:
        """Останов по суб-бюджету. Уже ждущая или терминальная задача не переводится повторно."""
        if task_id not in self._budget_stop:
            return False
        status = self._status(task_id)
        if status is None or status in TERMINAL_STATUSES or status is TaskStatus.waiting_approval:
            return True
        await self._checkpoint(task_id, session_id, plan, index)
        status = self._status(task_id)
        if status is None or status in TERMINAL_STATUSES or status is TaskStatus.waiting_approval:
            return True
        try:
            await self._apply_block(task_id, session_id, ApprovalReason.budget_exhausted.value)
        except InvalidTransition:
            status = self._status(task_id)
            if status is TaskStatus.waiting_approval or status in TERMINAL_STATUSES:
                return True
            raise
        return True

    async def _execute_plan(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        start: int,
        goal: str,
    ) -> None:
        self._remember_paid(task_id)
        if forecast_unknown(plan):
            await self._apply_block(task_id, session_id, "unknown_cost")
            return
        index = start
        while index < len(plan):
            if await self._stop_if_budget(task_id, session_id, plan, index):
                return
            if await self._hold_for_approval(task_id, session_id, plan, index):
                return
            if session_id in self._pause and session_id not in self._kill:
                await self._checkpoint(task_id, session_id, plan, index)
                await self._set_status(task_id, TaskStatus.paused, session_id=session_id)
                return
            if session_id in self._cancel or session_id in self._kill:
                await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
                return
            if session_id in self._steer:
                steered = await self._apply_steer(task_id, session_id, plan, index, goal)
                if steered is None:
                    await self._settle_stopped(task_id, session_id)
                    return
                plan, index = steered
                continue

            batch = _readonly_batch(plan, index, self.actions)
            step = batch[0]
            # Группа собирается только из read-only. Чужой шаг в батче — сбой,
            # не assert: под python -O assert исчез бы и шаг ушёл бы в исполнение.
            if len(batch) > 1 and any(not step_is_readonly(item, self.actions) for item in batch):
                await self._fail_closed(task_id, session_id, "parallel_risk")
                return
            risk = effective_risk(self.actions, step)
            if risk in APPROVAL_RISKS and self._risk_grant_key(task_id, index, step) not in self._risk_granted:
                await self._checkpoint(task_id, session_id, plan, index)
                await self._escalate(
                    task_id,
                    session_id,
                    "risk",
                    extra={"action": step.action, "risk": risk.value, "plan_risk": step.risk},
                )
                return

            if len(batch) == 1 and step.paid and self._running_paid_row(task_id, step) is not None:
                await self._hold_paid_unknown(task_id, session_id, step)
                return

            skip = self._skip_reason(task_id, step, plan, index) if len(batch) == 1 else None
            if skip:
                await self.bus.publish(
                    session_id,
                    "paid_retry_blocked" if skip == "paid_retry" else "step_skipped",
                    {"position": index, "action": step.action, "reason": skip},
                    task_id=task_id,
                )
                index += 1
                continue

            if any(item.paid and self._paid_outcome_unknown(task_id, item) for item in batch):
                # Строка running уже есть: шаг стартовал до рестарта, исход не записан.
                # Сам шаг не исполняется. Кнопки решают approve / retry / deny.
                self._block_reason[task_id] = "paid_outcome_unknown"
                await self._escalate(
                    task_id,
                    session_id,
                    "paid_outcome_unknown",
                    extra={
                        "action": step.action,
                        "position": index,
                        "forecast_usd": forecast_cost([step]),
                        "decisions": approval_decision_choices(),
                    },
                )
                return

            if any(item.paid for item in batch):
                if await self._stop_if_budget(task_id, session_id, plan, index):
                    return
                await self._checkpoint(task_id, session_id, plan, index)
            for item in batch:
                if step_is_readonly(item, self.actions):
                    continue
                outcome, verdict = await self._validate(task_id, session_id, item, "before")
                if outcome == "budget":
                    await self._halt_for_validator_budget(task_id, session_id, plan, index, verdict)
                    return
                if outcome == "missing_run":
                    return
                if outcome == "project":
                    return
                if outcome == "blocked":
                    await self._fail_validation(task_id, session_id)
                    return

            ctx = {
                "task_id": task_id,
                "session_id": session_id,
                "run_id": orch_run_id(task_id=task_id),
                "budget_scope": BudgetScope.pipeline.value,
            }
            started: list[tuple[PlanStep, int]] = []
            db_pos = self._next_step_position(task_id)
            for offset, item in enumerate(batch):
                await self.bus.publish(
                    session_id,
                    "step_started",
                    {"position": index + offset, "action": item.action, "paid": item.paid},
                    task_id=task_id,
                )
                started.append((item, self.tasks.insert_step(task_id, db_pos + offset, item)))
            try:
                if len(batch) == 1:
                    done_step, step_id = started[0]
                    results = [
                        await self._run_one(
                            session_id,
                            done_step,
                            ctx,
                            task_id=task_id,
                            step_id=step_id,
                            plan=plan,
                            index=index,
                        )
                    ]
                else:
                    results = await self._run_parallel(session_id, batch, ctx)
            except asyncio.CancelledError:
                if any(item.paid for item, _ in started):
                    self._shield_orphaned.add(task_id)
                    raise
                for _, step_id in started:
                    self.tasks.finish_step(step_id, "cancelled", None, 0.0)
                current = self._status(task_id)
                if current not in TERMINAL_STATUSES:
                    await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
                return
            except Exception:
                logger.exception("orch step {} failed", step.action)
                for _, step_id in started:
                    self.tasks.finish_step(step_id, "failed", None, 0.0)
                await self._set_status(task_id, TaskStatus.failed, session_id=session_id, error="step_failed")
                await self.bus.publish(
                    session_id,
                    "task_failed",
                    {"error": "step_failed", "action": step.action},
                    task_id=task_id,
                )
                return

            if any(isinstance(result, BaseException) for result in results):
                for (done_step, step_id), result in zip(started, results, strict=True):
                    if isinstance(result, BaseException):
                        state = "cancelled" if isinstance(result, asyncio.CancelledError) else "failed"
                        if self._claim_step(step_id):
                            self.tasks.finish_step(step_id, state, None, 0.0)
                        logger.warning("orch step {} failed in group: {}", done_step.action, result)
                    else:
                        await self._record(task_id, session_id, done_step, step_id, result, index)
                await self._set_status(task_id, TaskStatus.failed, session_id=session_id, error="step_failed")
                await self.bus.publish(
                    session_id,
                    "task_failed",
                    {"error": "step_failed", "action": step.action},
                    task_id=task_id,
                )
                return

            if task_id in self._paid_outcome_hold and any(item.paid for item, _ in started):
                if self._status(task_id) is TaskStatus.executing:
                    await self._mark_paid_outcome_unknown(session_id, task_id)
                return

            paid_in_batch = False
            rejected = False
            for offset, (item, result) in enumerate(zip(started, results, strict=True)):
                done_step, step_id = item
                if done_step.paid:
                    paid_in_batch = True
                if not await self._record(task_id, session_id, done_step, step_id, result, index + offset):
                    rejected = True
            if rejected:
                current = self._status(task_id)
                if current not in TERMINAL_STATUSES and current is not TaskStatus.waiting_approval:
                    await self._set_status(
                        task_id,
                        TaskStatus.failed,
                        session_id=session_id,
                        error="action_rejected",
                    )
                    await self.bus.publish(
                        session_id,
                        "task_failed",
                        {"error": "action_rejected", "action": step.action},
                        task_id=task_id,
                    )
                return

            next_index = index + len(batch)
            # last_response у ActionBackedExecutor остаётся None: LLM перед
            # действием нет. Кастомный исполнитель может выставить ответ роутера
            # (задел, пока WS2 не отдаёт бюджет из actions.execute).
            if await self._absorb_budget(
                task_id,
                session_id,
                getattr(self.executor, "last_response", None),
                position=next_index,
            ):
                return

            for offset, (item, _result) in enumerate(zip(started, results, strict=True)):
                done_step, _step_id = item
                if not step_is_readonly(done_step, self.actions):
                    outcome, verdict = await self._validate(task_id, session_id, done_step, "after")
                    if outcome == "budget":
                        await self._halt_for_validator_budget(
                            task_id,
                            session_id,
                            plan,
                            index + offset + 1,
                            verdict,
                        )
                        return
                    if outcome == "missing_run":
                        return
                    if outcome == "project":
                        return
                    if outcome == "blocked":
                        await self._fail_validation(task_id, session_id)
                        return

            index += len(batch)
            if any(done.mutating or done.paid for done, _ in started):
                await self._checkpoint(task_id, session_id, plan, index)
            else:
                self.tasks.mirror_progress(task_id, plan, index, self._spend(task_id))

            if session_id in self._cancel or session_id in self._kill:
                if paid_in_batch:
                    await self._checkpoint(task_id, session_id, plan, index)
                    await self._set_status(task_id, TaskStatus.paused, session_id=session_id)
                else:
                    await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
                return
            if session_id in self._pause:
                await self._checkpoint(task_id, session_id, plan, index)
                await self._set_status(task_id, TaskStatus.paused, session_id=session_id)
                return
            if await self._hold_for_approval(task_id, session_id, plan, index):
                return

        if await self._stop_if_budget(task_id, session_id, plan, index):
            return
        await self._set_status(task_id, TaskStatus.done, session_id=session_id)
        await self.bus.publish(session_id, "task_done", {"task_id": task_id}, task_id=task_id)

    async def _apply_steer(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        index: int,
        goal: str,
    ) -> tuple[list[PlanStep], int] | None:
        text = self._steer.pop(session_id)
        ctx = (self._context.get(task_id, "") + "\n" + text).strip()
        self._context[task_id] = ctx
        await self._checkpoint(task_id, session_id, plan, index)
        revised = await self._make_plan(task_id, session_id, goal, ctx)
        if revised is None:
            await self._settle_stopped(task_id, session_id)
            return None
        self._save_plan(task_id, revised, position=0)
        if await self._screen_plan(task_id, session_id, revised):
            return None
        await self._set_status(task_id, TaskStatus.critic_review, session_id=session_id)
        verdict = await self._review_plan(task_id, session_id, revised, ctx)
        if verdict is None:
            await self._settle_stopped(task_id, session_id)
            return None
        if verdict != "approve":
            await self._checkpoint(task_id, session_id, revised, 0)
            await self._apply_block(task_id, session_id, "steer_not_approved")
            return None
        self._approved[task_id] = _plan_fingerprint(revised)
        await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        return revised, 0

    async def _cancellable(self, session_id: str, awaitable: Any) -> Any:
        inner: asyncio.Task[Any] = asyncio.create_task(awaitable)
        self._unpaid[session_id] = inner
        try:
            return await inner
        finally:
            if self._unpaid.get(session_id) is inner:
                self._unpaid.pop(session_id, None)

    async def _run_one(
        self,
        session_id: str,
        step: PlanStep,
        ctx: dict[str, Any],
        *,
        task_id: int,
        step_id: int,
        plan: list[PlanStep],
        index: int,
    ) -> Any:
        async def _call() -> Any:
            return await self.executor.run(step, ctx)

        if step.paid:
            inner: asyncio.Task[Any] = asyncio.create_task(_call())
            self._paid_open.add(task_id)
            inner.add_done_callback(
                lambda done, _task=task_id, _session=session_id, _step=step, _sid=step_id, _plan=plan, _index=index: (
                    self._on_shield_done(
                        done,
                        task_id=_task,
                        session_id=_session,
                        step=_step,
                        step_id=_sid,
                        plan=_plan,
                        index=_index,
                    )
                )
            )
            try:
                return await asyncio.shield(inner)
            finally:
                self._paid_open.discard(task_id)
        return await self._cancellable(session_id, _call())

    def _on_shield_done(
        self,
        done: asyncio.Task[Any],
        *,
        task_id: int,
        session_id: str,
        step: PlanStep,
        step_id: int,
        plan: list[PlanStep],
        index: int,
    ) -> None:
        """Сторож: платный шаг дожил после отмены внешней задачи — записать результат."""
        if done.cancelled() or task_id not in self._shield_orphaned or task_id in self._paid_outcome_hold:
            return
        try:
            exc = done.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            if self._claim_step(step_id):
                self.tasks.finish_step(step_id, "failed", None, 0.0)
            self._shield_orphaned.discard(task_id)
            loop = asyncio.get_running_loop()
            job = loop.create_task(
                self._fail_closed(task_id, session_id, "paid_step_failed"),
                name=f"orch-shield-fail-{task_id}",
            )
            self._shield_jobs.add(job)
            job.add_done_callback(self._shield_jobs.discard)
            return
        if not self._claim_step(step_id):
            return
        self._write_step_done(task_id, step, step_id, done.result())
        loop = asyncio.get_running_loop()
        job = loop.create_task(
            self._pause_after_shield(task_id, session_id, plan, index + 1),
            name=f"orch-shield-{task_id}",
        )
        self._shield_jobs.add(job)
        job.add_done_callback(self._shield_jobs.discard)

    async def _pause_after_shield(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        position: int,
    ) -> None:
        self._shield_orphaned.discard(task_id)
        current = self._status(task_id)
        if current in TERMINAL_STATUSES or current is None:
            return
        await self._checkpoint(task_id, session_id, plan, position)
        if current is TaskStatus.executing:
            await self._set_status(task_id, TaskStatus.paused, session_id=session_id)

    def _claim_step(self, step_id: int) -> bool:
        if step_id in self._step_recorded:
            return False
        self._step_recorded.add(step_id)
        return True

    def _write_step_done(self, task_id: int, step: PlanStep, step_id: int, result: Any) -> None:
        ok = bool(getattr(result, "ok", False))
        cost = float(getattr(result, "cost", 0) or 0)
        change_id = getattr(result, "change_id", None) if ok else None
        self.tasks.finish_step(step_id, "done" if ok else "failed", change_id, cost)
        if ok and change_id:
            self._change_ids.setdefault(task_id, []).append(str(change_id))
        if ok and step.paid:
            self._paid_done.setdefault(task_id, set()).add(paid_step_identity(step.action, step.params))
        if cost:
            if step.paid:
                self.tasks.add_spend(task_id, actions=cost)
            else:
                self.tasks.add_spend(task_id, internal=cost)

    async def _run_parallel(self, session_id: str, steps: list[PlanStep], ctx: dict[str, Any]) -> list[Any]:
        return await self._cancellable(session_id, run_many(self.executor, steps, ctx))

    async def _validate(
        self,
        task_id: int,
        session_id: str,
        step: PlanStep,
        phase: str,
    ) -> tuple[str, ValidatorResult | None]:
        project_id = self._task_project_id(task_id)
        if project_id is None:
            if _step_requires_project(step, self.actions):
                await self._apply_block(task_id, session_id, ApprovalReason.missing_project_id.value)
                return "project", None
            return "ok", None
        names = ("no_deletions", "plan_sanity", "spend_guard") if phase == "after" else ("plan_sanity", "no_deletions")
        for name in names:
            result = await run_validator(
                self.validators,
                name,
                project_id,
                {"action": step.action, "phase": phase, "project_id": project_id},
                run_id=orch_run_id(task_id=task_id),
                task_id=task_id,
            )
            outcome = await self._publish_validator(task_id, session_id, result)
            if outcome == "missing_run":
                await self._fail_closed(task_id, session_id, "missing_run_id")
                return outcome, result
            if outcome != "ok":
                return outcome, result
        return "ok", None

    async def _dry_run(self, task_id: int, session_id: str, plan: list[PlanStep]) -> str:
        project_id = self._task_project_id(task_id)
        if project_id is None:
            # Платный/мутирующий план без id уже остановлен в _screen_plan.
            # Read-only без id валидаторы не зовёт.
            return "ok"
        payload = {
            VALIDATOR_CONTEXT_DRY_RUN: True,
            "project_id": project_id,
            "plan": plan_without_client_money(plan),
        }
        for name in _DRY_RUN:
            result = await run_validator(
                self.validators,
                name,
                project_id,
                payload,
                run_id=orch_run_id(task_id=task_id),
                task_id=task_id,
            )
            outcome = await self._publish_validator(task_id, session_id, result)
            if outcome == "missing_run":
                await self._fail_closed(task_id, session_id, "missing_run_id")
                return "missing_run"
            if outcome == "budget":
                await self._halt_for_validator_budget(task_id, session_id, plan, 0, result)
                return "budget"
            if outcome == "blocked":
                return "blocked"
        return "ok"

    async def _publish_validator(self, task_id: int, session_id: str, result: ValidatorResult) -> str:
        await self.bus.publish(
            session_id,
            "validator_result",
            result.model_dump(by_alias=True),
            task_id=task_id,
        )
        if bool(result.passed) or not bool(result.blocking):
            return "ok"
        if validator_missing_run_id(result):
            return "missing_run"
        if validator_budget_exhausted(result):
            return "budget"
        return "blocked"

    async def _halt_for_validator_budget(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        position: int,
        result: ValidatorResult | None,
    ) -> None:
        details = getattr(result, "details", None)
        payload = dict(details) if isinstance(details, dict) else {"details": details}
        approval = ApprovalEvent(
            task_id=task_id,
            reason=ApprovalReason.budget_exhausted.value,
            payload=payload,
        )
        self._budget_stop.add(task_id)
        await self._checkpoint(task_id, session_id, plan, position)
        extra = approval.model_dump()
        extra.pop("reason", None)
        await self._apply_block(task_id, session_id, approval.reason, extra=extra)

    async def _apply_block(
        self,
        task_id: int,
        session_id: str,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Статус блокировки берётся из ``BLOCK_OUTCOMES``, не из ветки вызова."""
        self._block_reason[task_id] = reason
        outcome = block_status(reason)
        if outcome is TaskStatus.waiting_approval:
            await self._escalate(task_id, session_id, reason, extra=extra)
            return
        if outcome is TaskStatus.cancelled:
            current = self._status(task_id)
            if current not in TERMINAL_STATUSES and current is not None:
                await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
            return
        await self._fail_closed(task_id, session_id, reason)

    async def _fail_validation(self, task_id: int, session_id: str) -> None:
        await self._apply_block(task_id, session_id, "validator_blocked")

    async def _fail_closed(self, task_id: int, session_id: str, error: str) -> None:
        current = self._status(task_id)
        if current in TERMINAL_STATUSES or current is TaskStatus.waiting_approval:
            return
        await self._set_status(task_id, TaskStatus.failed, session_id=session_id, error=error)
        await self.bus.publish(session_id, "task_failed", {"error": error}, task_id=task_id)

    async def _settle_stopped(self, task_id: int, session_id: str) -> None:
        """Ветка None оставляет waiting_approval или терминальный статус."""
        status = self._status(task_id)
        if status is None or status in TERMINAL_STATUSES or status is TaskStatus.waiting_approval:
            return
        if task_id in self._budget_stop and can_transition(status, block_status(ApprovalReason.budget_exhausted.value)):
            await self._apply_block(task_id, session_id, ApprovalReason.budget_exhausted.value)
            return
        if can_transition(status, TaskStatus.failed):
            await self._fail_closed(task_id, session_id, "plan_stopped")

    def _task_project_id(self, task_id: int) -> int | None:
        raw = (self.tasks.get(task_id) or {}).get("project_id")
        if raw is None:
            return None
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        return value

    async def _record(
        self,
        task_id: int,
        session_id: str,
        step: PlanStep,
        step_id: int,
        result: Any,
        position: int,
    ) -> bool:
        """True — шаг успешен или уже записан. ``ok=False`` не done и не paid-retry."""
        ok = bool(getattr(result, "ok", False))
        if not self._claim_step(step_id):
            return True
        self._write_step_done(task_id, step, step_id, result)
        await self.bus.publish(
            session_id,
            "step_finished",
            {
                "position": position,
                "action": step.action,
                "ok": bool(getattr(result, "ok", False)),
                "change_id": getattr(result, "change_id", None),
            },
            task_id=task_id,
        )
        return ok

    def _skip_reason(
        self,
        task_id: int,
        step: PlanStep,
        plan: list[PlanStep],
        index: int,
    ) -> str | None:
        if self._change_already_applied(task_id, step, plan, index):
            return "change_already_applied"
        if step.paid and paid_step_identity(step.action, step.params) in self._paid_done.get(task_id, set()):
            return "paid_retry"
        return None

    def _change_already_applied(
        self,
        task_id: int,
        step: PlanStep,
        plan: list[PlanStep],
        index: int,
    ) -> bool:
        """Повтор шага, у которого в ``orch_steps.change_id`` уже есть сдача.

        Параметр ``_change_id`` плана не источник. Считаются только done-строки
        с непустым change_id и теми же публичными params.
        """
        ident = _public_params(step.params)
        prior = sum(
            1 for item in plan[:index] if item.action == step.action and _public_params(item.params) == ident
        )
        done = 0
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) != "done" or not row.get("change_id"):
                continue
            if str(row.get("action_name") or "") != step.action:
                continue
            try:
                stored = json.loads(row.get("params_json") or "{}")
            except json.JSONDecodeError:
                stored = {}
            if isinstance(stored, dict) and _public_params(stored) == ident:
                done += 1
        return done > prior

    def _next_step_position(self, task_id: int) -> int:
        rows = self.tasks.list_steps(task_id)
        if not rows:
            return 0
        return max(int(row["position"]) for row in rows) + 1

    def _running_paid_row(self, task_id: int, step: PlanStep) -> dict[str, Any] | None:
        """Платная строка ``running`` с тем же действием и публичными params."""
        if not step.paid:
            return None
        ident = _public_params(step.params)
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) != "running" or not row.get("paid"):
                continue
            if str(row.get("action_name") or "") != step.action:
                continue
            try:
                stored = json.loads(row.get("params_json") or "{}")
            except json.JSONDecodeError:
                stored = {}
            if isinstance(stored, dict) and _public_params(stored) == ident:
                return row
        return None

    async def _mark_paid_outcome_unknown(self, session_id: str, task_id: int) -> None:
        """Kill или interrupt во время платного шага: исход неизвестен, отката нет."""
        if task_id not in self._paid_outcome_hold:
            return
        status = self._status(task_id)
        if status is None or status in TERMINAL_STATUSES or status is TaskStatus.waiting_approval:
            return
        action = ""
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) == "running" and row.get("paid"):
                action = str(row.get("action_name") or "")
                break
        await self._apply_block(
            task_id,
            session_id,
            "paid_outcome_unknown",
            extra={"action": action} if action else None,
        )

    async def _hold_paid_unknown(self, task_id: int, session_id: str, step: PlanStep) -> None:
        await self._apply_block(
            task_id,
            session_id,
            "paid_unknown_outcome",
            extra={"action": step.action},
        )

    def _remember_paid(self, task_id: int) -> None:
        found = self._paid_done.setdefault(task_id, set())
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) != "done" or not row.get("paid"):
                continue
            try:
                params = json.loads(row.get("params_json") or "{}")
            except json.JSONDecodeError:
                params = {}
            if not isinstance(params, dict):
                params = {}
            found.add(paid_step_identity(str(row.get("action_name") or ""), params))

    async def _checkpoint(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        position: int,
    ) -> None:
        data = CheckpointData(
            plan=list(plan),
            position=position,
            change_ids=list(self._change_ids.get(task_id, [])),
            spend=self._spend(task_id),
            context=self._context.get(task_id, ""),
        )
        self.tasks.mirror_progress(task_id, plan, position, data.spend)
        await asyncio.to_thread(self.checkpoints.save, task_id, session_id, data)
        await self.bus.publish(
            session_id,
            "checkpoint",
            data.model_dump(),
            task_id=task_id,
        )

    async def _hold_for_approval(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        index: int,
    ) -> bool:
        reason = self._approval.pop(session_id, None)
        if reason is None:
            return False
        await self._checkpoint(task_id, session_id, plan, index)
        await self._escalate(task_id, session_id, reason)
        return True

    async def _escalate(
        self,
        task_id: int,
        session_id: str,
        reason: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        await self._set_status(task_id, TaskStatus.waiting_approval, session_id=session_id)
        payload: dict[str, Any] = {"reason": reason, "task_id": task_id}
        if extra:
            payload.update(extra)
        await self.bus.publish(session_id, "approval_required", payload, task_id=task_id)

    async def _set_status(
        self,
        task_id: int | None,
        status: TaskStatus,
        *,
        session_id: str,
        goal: str = "",
        error: str | None = None,
        project_id: int | None = None,
    ) -> int:
        if task_id is None:
            if status is not TaskStatus.queued:
                raise InvalidTransition("новая задача начинается со статуса queued")
            task_id = self.tasks.insert(session_id, goal, status.value, project_id=project_id)
        else:
            current = self._status(task_id)
            if current is None or not can_transition(current, status):
                src = current.value if current is not None else "missing"
                raise InvalidTransition(f"{src} -> {status.value}")
            self.tasks.set_status(task_id, status.value, error=error)
        self.history.append((task_id, status.value))
        await self.bus.publish(
            session_id,
            "agent_status",
            {"status": status.value, "agent": "supervisor"},
            task_id=task_id,
        )
        if status in TERMINAL_STATUSES:
            await self._start_queued(session_id, task_id)
        return task_id

    async def _start_queued(self, session_id: str, finished_id: int) -> None:
        """Дренаж только после терминального статуса.

        Память очереди заполняет ``submit_task`` и ``recover_after_restart``
        (строки ``queued`` из БД). ``paused`` и ``waiting_approval`` очередь
        не отпускают: ``resume`` и ``grant_approval`` продолжают ту же задачу,
        вторая рядом не стартует.
        """
        if self._session_task.get(session_id) not in {None, finished_id}:
            return
        if session_id in self._kill:
            return
        pending = self._queued.get(session_id) or []
        if not pending:
            return
        task_id, goal = pending.pop(0)
        self._cancel.discard(session_id)
        self._pause.discard(session_id)
        self._steer.pop(session_id, None)
        self._approval.pop(session_id, None)
        self._session_task[session_id] = task_id
        self._change_ids[task_id] = []
        self._context[task_id] = ""
        self._paid_done[task_id] = set()
        self._runs[task_id] = asyncio.create_task(
            self._drive(task_id, session_id, goal),
            name=f"orch-task-{task_id}",
        )

    def _status(self, task_id: int) -> TaskStatus | None:
        row = self.tasks.get(task_id)
        if row is None:
            return None
        return TaskStatus(str(row["status"]))

    def _spend(self, task_id: int) -> SpendSnapshot:
        row = self.tasks.get(task_id) or {}
        return SpendSnapshot(
            internal=float(row.get("spend_internal") or 0),
            actions=float(row.get("spend_actions") or 0),
        )


def _plan_fingerprint(plan: list[PlanStep]) -> str:
    payload = [step.model_dump() for step in plan]
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _step_requires_project(step: PlanStep, actions: Any) -> bool:
    """Не read-only по реестру, либо план ужесточил шаг (paid / mutating / риск)."""
    return not step_is_readonly(step, actions)


def _plan_requires_project(plan: list[PlanStep], actions: Any) -> bool:
    return any(_step_requires_project(step, actions) for step in plan)


def _public_params(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if not str(key).startswith("_")}


def _readonly_batch(plan: list[PlanStep], index: int, actions: Any) -> list[PlanStep]:
    step = plan[index]
    group = step.parallel_group
    if not group or not _can_run_parallel(step, actions):
        return [step]
    batch = [step]
    cursor = index + 1
    while cursor < len(plan):
        nxt = plan[cursor]
        if nxt.parallel_group != group or not _can_run_parallel(nxt, actions):
            break
        batch.append(nxt)
        cursor += 1
    return batch


def _can_run_parallel(step: PlanStep, actions: Any) -> bool:
    return step_is_readonly(step, actions)
