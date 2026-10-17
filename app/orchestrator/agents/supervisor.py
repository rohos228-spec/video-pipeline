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
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import (
    APPROVAL_RISKS,
    TERMINAL_STATUSES,
    VALIDATOR_CONTEXT_DRY_RUN,
    CheckpointData,
    PlanStep,
    SpendSnapshot,
    TaskStatus,
    can_transition,
    paid_step_identity,
    plan_deletions,
    plan_missing_verify,
)
from app.orchestrator.events import EventBus

_DRY_RUN = ("plan_sanity", "no_deletions", "spend_guard")


class InvalidTransition(RuntimeError):
    pass


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
        self._risk_granted: set[tuple[int, int]] = set()
        self._paid_done: dict[int, set[str]] = {}

    async def start_task(self, session_id: str, goal: str) -> int:
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

    async def wait(self, task_id: int, timeout: float | None = None) -> None:
        await asyncio.wait_for(self._runs[task_id], timeout)

    async def resume(self, task_id: int) -> None:
        row = self.tasks.get(task_id)
        if row is None:
            raise LookupError(task_id)
        data = self.checkpoints.load_latest(task_id)
        if data is None:
            raise LookupError(f"нет чекпоинта для задачи {task_id}")
        session_id = str(row["session_id"])
        self._arm_resume(session_id, task_id, data)
        await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
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

    async def grant_approval(self, session_id: str) -> None:
        """Продолжить с чекпоинта после waiting_approval. Риск этого шага уже принят."""
        task_id = self._session_task.get(session_id)
        if task_id is None:
            raise LookupError(session_id)
        if self._status(task_id) is not TaskStatus.waiting_approval:
            raise InvalidTransition("grant_approval только из waiting_approval")
        data = self.checkpoints.load_latest(task_id)
        if data is None:
            raise LookupError(f"нет чекпоинта для задачи {task_id}")
        self._risk_granted.add((task_id, int(data.position)))
        self._arm_resume(session_id, task_id, data)
        await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        goal = str((self.tasks.get(task_id) or {}).get("goal") or "")
        self._runs[task_id] = asyncio.create_task(
            self._execute_plan(task_id, session_id, list(data.plan), int(data.position), goal),
            name=f"orch-approve-{task_id}",
        )

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
        """Взвести cancel-флаг и отменить текущий неплатный шаг, если он есть."""
        self._cancel.add(session_id)
        task_id = self._session_task.get(session_id)
        if task_id is not None:
            self.tasks.set_flag(task_id, cancel_requested=1)
        inflight = self._unpaid.get(session_id)
        if inflight is not None and not inflight.done():
            inflight.cancel()

    def request_kill(self, session_id: str) -> None:
        """Kill switch §3.3: неплатное — сразу, новые действия запрещены, стоп-ключ взведён.

        Платный шаг этим вызовом не обрывается: пауза на его границе.
        """
        self._kill.add(session_id)
        self.request_cancel(session_id)
        task_id = self._session_task.get(session_id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._publish_kill(session_id, task_id))

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
        return self._session_task.get(session_id)

    async def _publish_kill(self, session_id: str, task_id: int | None) -> None:
        await self.bus.publish(
            session_id,
            "kill_switch",
            {"stop_key_set": True, "only_node": True, "task_id": task_id},
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
        await self._set_status(task_id, TaskStatus.planning, session_id=session_id)
        plan = list(await self._cancellable(session_id, self.planner.make_plan(goal, context=context)))
        self.tasks.save_plan(task_id, plan)
        revises = 0
        while True:
            blocked = await self._screen_plan(task_id, session_id, plan)
            if blocked:
                return None
            await self._set_status(task_id, TaskStatus.critic_review, session_id=session_id)
            verdict = str(await self._cancellable(session_id, self.critic.review(plan, context=context)))
            if verdict == "approve":
                return plan
            if verdict == "reject":
                await self._escalate(task_id, session_id, "critic_reject")
                return None
            revises += 1
            if revises > 2:
                await self._escalate(task_id, session_id, "critic_escalation")
                return None
            await self._set_status(task_id, TaskStatus.planning, session_id=session_id)
            plan = list(
                await self._cancellable(
                    session_id,
                    self.planner.make_plan(goal, context=context or "revise"),
                )
            )
            self.tasks.save_plan(task_id, plan)

    async def _screen_plan(self, task_id: int, session_id: str, plan: list[PlanStep]) -> bool:
        """Dry-run валидаторов и локальные правила 9 / запрет удалений. True — план отклонён."""
        if not plan:
            await self._escalate(task_id, session_id, "plan_sanity")
            return True
        if not await self._dry_run(task_id, session_id, plan):
            await self._escalate(task_id, session_id, "validator_blocked")
            return True
        if plan_deletions(plan):
            await self._escalate(task_id, session_id, "deletion_blocked")
            return True
        if plan_missing_verify(plan):
            await self._escalate(task_id, session_id, "plan_sanity")
            return True
        return False

    async def _execute_plan(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        start: int,
        goal: str,
    ) -> None:
        self._remember_paid(task_id)
        index = start
        while index < len(plan):
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
                    return
                plan, index = steered
                continue

            batch = _readonly_batch(plan, index, self.actions)
            step = batch[0]
            risk = effective_risk(self.actions, step)
            if risk in APPROVAL_RISKS and (task_id, index) not in self._risk_granted:
                await self._checkpoint(task_id, session_id, plan, index)
                await self._escalate(
                    task_id,
                    session_id,
                    "risk",
                    extra={"action": step.action, "risk": risk.value, "plan_risk": step.risk},
                )
                return

            skip = self._skip_reason(task_id, step) if len(batch) == 1 else None
            if skip:
                await self.bus.publish(
                    session_id,
                    "paid_retry_blocked" if skip == "paid_retry" else "step_skipped",
                    {"position": index, "action": step.action, "reason": skip},
                    task_id=task_id,
                )
                index += 1
                continue

            if step.paid:
                await self._checkpoint(task_id, session_id, plan, index)
            for item in batch:
                if item.mutating and not await self._validate(task_id, session_id, item, "before"):
                    await self._fail_validation(task_id, session_id)
                    return

            ctx = {"task_id": task_id, "session_id": session_id}
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
                    results = [await self._run_one(session_id, batch[0], ctx)]
                else:
                    results = await self._run_parallel(session_id, batch, ctx)
            except asyncio.CancelledError:
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

            paid_in_batch = False
            for offset, (item, result) in enumerate(zip(started, results, strict=True)):
                done_step, step_id = item
                await self._record(task_id, session_id, done_step, step_id, result, index + offset)
                if done_step.paid:
                    paid_in_batch = True
                    self._paid_done.setdefault(task_id, set()).add(
                        paid_step_identity(done_step.action, done_step.params)
                    )
                if done_step.mutating and not await self._validate(task_id, session_id, done_step, "after"):
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
        await self._set_status(task_id, TaskStatus.planning, session_id=session_id)
        revised = list(await self._cancellable(session_id, self.planner.make_plan(goal, context=ctx)))
        self.tasks.save_plan(task_id, revised, position=0)
        if await self._screen_plan(task_id, session_id, revised):
            return None
        await self._set_status(task_id, TaskStatus.critic_review, session_id=session_id)
        verdict = str(await self._cancellable(session_id, self.critic.review(revised, context=ctx)))
        if verdict != "approve":
            await self._escalate(task_id, session_id, "steer_not_approved")
            return None
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

    async def _run_one(self, session_id: str, step: PlanStep, ctx: dict[str, Any]) -> Any:
        async def _call() -> Any:
            return await self.executor.run(step, ctx)

        if step.paid:
            inner: asyncio.Task[Any] = asyncio.create_task(_call())
            return await asyncio.shield(inner)
        return await self._cancellable(session_id, _call())

    async def _run_parallel(self, session_id: str, steps: list[PlanStep], ctx: dict[str, Any]) -> list[Any]:
        return await self._cancellable(session_id, run_many(self.executor, steps, ctx))

    async def _validate(self, task_id: int, session_id: str, step: PlanStep, phase: str) -> bool:
        project_id = int(step.params.get("project_id") or 0)
        names = ("no_deletions", "plan_sanity", "spend_guard") if phase == "after" else ("plan_sanity", "no_deletions")
        for name in names:
            result = await self.validators.run(
                name,
                project_id,
                {"action": step.action, "task_id": task_id, "phase": phase},
            )
            await self.bus.publish(
                session_id,
                "validator_result",
                result.model_dump(by_alias=True),
                task_id=task_id,
            )
            if not bool(result.passed) and bool(result.blocking):
                return False
        return True

    async def _dry_run(self, task_id: int, session_id: str, plan: list[PlanStep]) -> bool:
        project_id = 0
        for step in plan:
            if step.params.get("project_id"):
                project_id = int(step.params["project_id"])
                break
        payload = {
            VALIDATOR_CONTEXT_DRY_RUN: True,
            "plan": [step.model_dump() for step in plan],
            "task_id": task_id,
        }
        for name in _DRY_RUN:
            result = await self.validators.run(name, project_id, payload)
            await self.bus.publish(
                session_id,
                "validator_result",
                result.model_dump(by_alias=True),
                task_id=task_id,
            )
            if not bool(result.passed) and bool(result.blocking):
                return False
        return True

    async def _fail_validation(self, task_id: int, session_id: str) -> None:
        await self._set_status(
            task_id,
            TaskStatus.failed,
            session_id=session_id,
            error="validator_blocked",
        )
        await self.bus.publish(
            session_id,
            "task_failed",
            {"error": "validator_blocked"},
            task_id=task_id,
        )

    async def _record(
        self,
        task_id: int,
        session_id: str,
        step: PlanStep,
        step_id: int,
        result: Any,
        position: int,
    ) -> None:
        cost = float(getattr(result, "cost", 0) or 0)
        change_id = getattr(result, "change_id", None)
        self.tasks.finish_step(step_id, "done", change_id, cost)
        if change_id:
            self._change_ids.setdefault(task_id, []).append(str(change_id))
        if cost:
            if step.paid:
                self.tasks.add_spend(task_id, actions=cost)
            else:
                self.tasks.add_spend(task_id, internal=cost)
        await self.bus.publish(
            session_id,
            "step_finished",
            {
                "position": position,
                "action": step.action,
                "ok": bool(getattr(result, "ok", False)),
                "change_id": change_id,
            },
            task_id=task_id,
        )

    def _skip_reason(self, task_id: int, step: PlanStep) -> str | None:
        change_id = step.params.get("_change_id")
        if change_id and str(change_id) in self._change_ids.get(task_id, []):
            return "change_already_applied"
        if step.paid and paid_step_identity(step.action, step.params) in self._paid_done.get(task_id, set()):
            return "paid_retry"
        return None

    def _next_step_position(self, task_id: int) -> int:
        rows = self.tasks.list_steps(task_id)
        if not rows:
            return 0
        return max(int(row["position"]) for row in rows) + 1

    def _remember_paid(self, task_id: int) -> None:
        found = self._paid_done.setdefault(task_id, set())
        for row in self.tasks.list_steps(task_id):
            if str(row.get("status")) != "done" or not row.get("paid"):
                continue
            params = json.loads(row.get("params_json") or "{}")
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
        self.checkpoints.save(task_id, session_id, data)
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
    ) -> int:
        if task_id is None:
            if status is not TaskStatus.queued:
                raise InvalidTransition("новая задача начинается со статуса queued")
            task_id = self.tasks.insert(session_id, goal, status.value)
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
        return task_id

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
    return not step.mutating and not step.paid and effective_risk(actions, step) not in APPROVAL_RISKS
