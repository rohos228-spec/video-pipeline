"""Старт runtime вызывает восстановление, а не только прямой метод."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import CheckpointData, PlanStep, TaskStatus
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter


def test_web_lifespan_starts_the_orchestrator() -> None:
    source = Path(__file__).resolve().parents[2] / "app" / "web" / "api.py"
    assert "await get_runtime().start()" in source.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_runtime_start_recovers_executing_task(token: str) -> None:
    del token
    step = PlanStep(action="look")
    task_id = TaskStore().insert("boot-recover", "смотри", TaskStatus.executing.value, project_id=13)
    CheckpointStore().save(task_id, "boot-recover", CheckpointData(plan=[step], position=0))
    actions = MockActions()
    runtime = build_runtime(actions=actions, validators=MockValidators(), router=ScriptedRouter())
    set_runtime(runtime)
    try:
        started = await runtime.start()
        assert task_id in started
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert [call["name"] for call in actions.calls] == ["look"]
    finally:
        set_runtime(None)
