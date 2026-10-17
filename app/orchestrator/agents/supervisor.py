"""Супервизор: единственная точка записи статусов FSM.

R1: planner / critic / executor — заглушки. Есть cancel-флаг и пауза
на границе шага. Неплатный шаг отменяется через ``asyncio.Task.cancel``,
платный не рвётся mid-flight (``asyncio.shield``), пауза — после него.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

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
    TERMINAL_STATUSES,
    CheckpointData,
    PlanStep,
    SpendSnapshot,
    TaskStatus,
    can_transition,
)
from app.orchestrator.events import EventBus


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
        self._steer: dict[str, str] = {}
        self._session_task: dict[str, int] = {}
        self._change_ids: dict[int, list[str]] = {}
        self._context: dict[int, str] = {}

    async def start_task(self, session_id: str, goal: str) -> int:
        self._cancel.discard(session_id)
        self._pause.discard(session_id)
        self._steer.pop(session_id, None)
        task_id = await self._set_status(
            None,
            TaskStatus.queued,
            session_id=session_id,
            goal=goal,
        )
        self._session_task[session_id] = task_id
        self._change_ids[task_id] = []
        self._context[task_id] = ""
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
        self._cancel.discard(session_id)
        self._pause.discard(session_id)
        self._steer.pop(session_id, None)
        self._session_task[session_id] = task_id
        self._change_ids[task_id] = list(data.change_ids)
        self._context[task_id] = data.context
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

    def request_cancel(self, session_id: str) -> None:
        """Взвести cancel-флаг и отменить текущий неплатный шаг, если он есть."""
        self._cancel.add(session_id)
        task_id = self._session_task.get(session_id)
        if task_id is not None:
            self.tasks.set_flag(task_id, cancel_requested=1)
        inflight = self._unpaid.get(session_id)
        if inflight is not None and not inflight.done():
            inflight.cancel()

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

    def active_task(self, session_id: str) -> int | None:
        return self._session_task.get(session_id)

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
        plan = list(await self.planner.make_plan(goal, context=context))
        self.tasks.save_plan(task_id, plan)
        revises = 0
        while True:
            await self._set_status(task_id, TaskStatus.critic_review, session_id=session_id)
            verdict = str(await self.critic.review(plan, context=context))
            if verdict == "approve":
                return plan
            if verdict == "reject":
                await self._escalate(task_id, session_id, "critic_reject")
                return None
            revises += 1
            if revises > 2:
                await self._escalate(task_id, session_id, "revise_limit")
                return None
            await self._set_status(task_id, TaskStatus.planning, session_id=session_id)
            plan = list(await self.planner.make_plan(goal, context=context or "revise"))
            self.tasks.save_plan(task_id, plan)

    async def _execute_plan(
        self,
        task_id: int,
        session_id: str,
        plan: list[PlanStep],
        start: int,
        goal: str,
    ) -> None:
        index = start
        while index < len(plan):
            if session_id in self._pause:
                await self._checkpoint(task_id, session_id, plan, index)
                await self._set_status(task_id, TaskStatus.paused, session_id=session_id)
                return
            if session_id in self._cancel:
                await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
                return
            if session_id in self._steer:
                steered = await self._apply_steer(task_id, session_id, plan, index, goal)
                if steered is None:
                    return
                plan, index = steered
                continue

            step = plan[index]
            if step.paid:
                await self._checkpoint(task_id, session_id, plan, index)
            if step.mutating and not await self._validate(task_id, session_id, step):
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
                return

            await self.bus.publish(
                session_id,
                "step_started",
                {"position": index, "action": step.action, "paid": step.paid},
                task_id=task_id,
            )
            step_id = self.tasks.insert_step(task_id, index, step)
            try:
                result = await self._run_one(session_id, step, {"task_id": task_id, "session_id": session_id})
            except asyncio.CancelledError:
                self.tasks.finish_step(step_id, "cancelled", None, 0.0)
                current = self._status(task_id)
                if current not in TERMINAL_STATUSES:
                    await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
                return
            except Exception:
                logger.exception("orch step {} failed", step.action)
                self.tasks.finish_step(step_id, "failed", None, 0.0)
                await self._set_status(task_id, TaskStatus.failed, session_id=session_id, error="step_failed")
                await self.bus.publish(
                    session_id,
                    "task_failed",
                    {"error": "step_failed", "action": step.action},
                    task_id=task_id,
                )
                return

            cost = float(result.cost or 0)
            self.tasks.finish_step(step_id, "done", result.change_id, cost)
            if result.change_id:
                self._change_ids.setdefault(task_id, []).append(result.change_id)
            if cost:
                if step.paid:
                    self.tasks.add_spend(task_id, actions=cost)
                else:
                    self.tasks.add_spend(task_id, internal=cost)
            await self.bus.publish(
                session_id,
                "step_finished",
                {
                    "position": index,
                    "action": step.action,
                    "ok": result.ok,
                    "change_id": result.change_id,
                },
                task_id=task_id,
            )
            index += 1
            if step.mutating or step.paid:
                await self._checkpoint(task_id, session_id, plan, index)
            else:
                self.tasks.mirror_progress(task_id, plan, index, self._spend(task_id))

            if session_id in self._cancel:
                if step.paid:
                    await self._checkpoint(task_id, session_id, plan, index)
                    await self._set_status(task_id, TaskStatus.paused, session_id=session_id)
                else:
                    await self._set_status(task_id, TaskStatus.cancelled, session_id=session_id)
                return
            if session_id in self._pause:
                await self._checkpoint(task_id, session_id, plan, index)
                await self._set_status(task_id, TaskStatus.paused, session_id=session_id)
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
        revised = list(await self.planner.make_plan(goal, context=ctx))
        self.tasks.save_plan(task_id, revised, position=0)
        await self._set_status(task_id, TaskStatus.critic_review, session_id=session_id)
        verdict = str(await self.critic.review(revised, context=ctx))
        if verdict != "approve":
            await self._escalate(task_id, session_id, "steer_not_approved")
            return None
        await self._set_status(task_id, TaskStatus.executing, session_id=session_id)
        return revised, 0

    async def _run_one(self, session_id: str, step: PlanStep, ctx: dict[str, Any]) -> Any:
        async def _call() -> Any:
            return await self.executor.run(step, ctx)

        if step.paid:
            inner: asyncio.Task[Any] = asyncio.create_task(_call())
            return await asyncio.shield(inner)
        inner = asyncio.create_task(_call())
        self._unpaid[session_id] = inner
        try:
            return await inner
        finally:
            if self._unpaid.get(session_id) is inner:
                self._unpaid.pop(session_id, None)

    async def _validate(self, task_id: int, session_id: str, step: PlanStep) -> bool:
        project_id = int(step.params.get("project_id") or 0)
        result = await self.validators.run(
            "plan_sanity",
            project_id,
            {"action": step.action, "task_id": task_id},
        )
        passed = bool(result.passed)
        await self.bus.publish(
            session_id,
            "validator_result",
            result.model_dump(by_alias=True),
            task_id=task_id,
        )
        return passed or not bool(result.blocking)

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

    async def _escalate(self, task_id: int, session_id: str, reason: str) -> None:
        await self._set_status(task_id, TaskStatus.waiting_approval, session_id=session_id)
        await self.bus.publish(
            session_id,
            "approval_required",
            {"reason": reason},
            task_id=task_id,
        )

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
