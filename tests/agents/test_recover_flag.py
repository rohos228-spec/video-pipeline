"""Рестарт оркестратора только по флагу и не поверх stop-ключа."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.agents.runtime import build_runtime, recover_on_start, set_runtime
from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import CheckpointData, PlanStep, TaskStatus
from app.services.step_cancel import clear_stop, request_stop
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter


def test_recover_on_start_is_off_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCH_RECOVER_ON_START", raising=False)
    assert recover_on_start() is False
    monkeypatch.setenv("ORCH_RECOVER_ON_START", "1")
    assert recover_on_start() is True
    source = Path("app/web/api.py").read_text(encoding="utf-8")
    assert "recover_on_start()" in source


@pytest.mark.asyncio
async def test_recover_skips_a_task_when_the_stop_key_is_set(token: str) -> None:
    del token
    project_id = 913370
    session = "recover-stop"
    step = PlanStep(action="next", expected="go", mutating=True)
    tasks = TaskStore()
    task_id = tasks.insert(session, "доделать", TaskStatus.executing.value, project_id=project_id)
    tasks.save_plan(task_id, [step], position=0)
    CheckpointStore().save(task_id, session, CheckpointData(plan=[step], position=0))
    request_stop(project_id)
    actions = MockActions()
    runtime = build_runtime(actions=actions, validators=MockValidators(), router=ScriptedRouter())
    set_runtime(runtime)
    try:
        started = await runtime.supervisor.recover_after_restart()
        assert task_id not in started
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.executing.value
    finally:
        clear_stop(project_id)
        set_runtime(None)
