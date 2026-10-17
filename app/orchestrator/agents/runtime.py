"""Сборка процесса оркестратора (один uvicorn-worker, объекты в памяти)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

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
            _runtime = build_runtime()
    return _runtime


def set_runtime(runtime: Runtime | None) -> None:
    global _runtime
    _runtime = runtime
