"""Сборка процесса оркестратора (один uvicorn-worker, объекты в памяти)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from app.orchestrator.agents.critic import RouterCritic
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.stubs import FallbackRouter, StubCritic, StubPlanner
from app.orchestrator.agents.supervisor import Supervisor
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.db.migrations import ensure_migrated
from app.orchestrator.events import EventBus
from app.orchestrator.queue import MessageQueue


@dataclass
class Runtime:
    bus: EventBus
    supervisor: Supervisor
    queue: MessageQueue
    checkpoints: CheckpointStore
    tasks: TaskStore
    router: Any
    runtime_degraded: tuple[str, ...] = ()
    compaction: Any = None

    async def shutdown(self) -> None:
        job = self.compaction
        if job is not None:
            await job.shutdown()

    async def start(self) -> list[int]:
        """Старт процесса: продолжить задачи, оставшиеся после рестарта."""
        return await self.supervisor.recover_after_restart()


def recover_on_start() -> bool:
    """Поднимать задачи при старте процесса только по явному флагу.

    Пустое значение и всё, кроме 1/true/yes/on, — выключено.
    """
    raw = os.environ.get("ORCH_RECOVER_ON_START", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def build_runtime(
    *,
    actions: Any | None = None,
    validators: Any | None = None,
    router: Any | None = None,
    planner: Any | None = None,
    critic: Any | None = None,
    executor: Any | None = None,
) -> Runtime:
    ensure_migrated()
    bus = EventBus()
    checkpoints = CheckpointStore()
    tasks = TaskStore()
    bound_router = router or FallbackRouter()
    supervisor = Supervisor(
        bus,
        checkpoints,
        tasks=tasks,
        actions=actions,
        validators=validators,
        planner=planner or StubPlanner(),
        critic=critic or StubCritic(),
        executor=executor,
    )
    queue = MessageQueue(bus, bound_router, supervisor)
    return Runtime(
        bus=bus,
        supervisor=supervisor,
        queue=queue,
        checkpoints=checkpoints,
        tasks=tasks,
        router=bound_router,
    )


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    """Один runtime на процесс.

    ``ORCH_RUNTIME=r6-fixture`` (и только это значение) подставляет приёмку R6.
    Без переменной — прежняя прод-сборка. Гейт ``ORCH_TEST=1`` проверяет testkit.
    """
    global _runtime
    if _runtime is None:
        if os.environ.get("ORCH_RUNTIME", "").strip() == "r6-fixture":
            from app.orchestrator.agents.testkit import build_r6_runtime

            _runtime = build_r6_runtime()
        else:
            _runtime = build_production_runtime()
    return _runtime


def build_production_runtime() -> Runtime:
    """Прод: planner/critic ходят в ``router.call``/``classify``, если пакет есть."""
    from app.orchestrator.agents.wiring import load_actions, load_rag, load_router, load_validators

    router, missing_router = load_router()
    rag, missing_rag = load_rag()
    validators, missing_validators = load_validators()
    actions, missing_actions = load_actions()
    degraded = [
        name
        for name in (missing_router, missing_rag, missing_validators, missing_actions)
        if name
    ]
    runtime = build_runtime(
        actions=actions,
        validators=validators,
        router=router,
        planner=RouterPlanner(router, rag),
        critic=RouterCritic(router, rag),
    )
    runtime.runtime_degraded = tuple(degraded)
    from app.orchestrator.agents.compaction import attach_compaction

    attach_compaction(runtime)
    return runtime


def set_runtime(runtime: Runtime | None) -> None:
    global _runtime
    previous = _runtime
    _runtime = runtime
    if previous is not None and previous is not runtime:
        job = getattr(previous, "compaction", None)
        if job is not None:
            job.request_stop()
