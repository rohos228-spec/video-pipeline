"""Interrupt: неплатный шаг отменяется, платный доживает до границы."""

from __future__ import annotations

import time

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


@pytest.mark.asyncio
async def test_interrupt_cancels_unpaid_mock_step(token: str) -> None:
    del token
    session = "stop-1"
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(
                    action="mock_sleep",
                    params={"_block": True, "_seconds": 30},
                    expected="wait",
                    paid=False,
                    mutating=False,
                )
            ]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(session, "долгий неоплачиваемый")
        await actions.started.wait()
        started = time.perf_counter()
        await rt.queue.accept(session, "отмена")
        await rt.supervisor.wait(task_id, timeout=8)
        elapsed = time.perf_counter() - started
        assert elapsed < 10.0
        assert rt.tasks.get(task_id)["status"] == TaskStatus.cancelled.value
        assert rt.tasks.list_steps(task_id)[0]["status"] == "cancelled"
        events = rt.bus.replay(session, 0)
        ack = next(event for event in events if event.type == "ack")
        classification = next(event for event in events if event.type == "classification")
        assert ack.seq < classification.seq
        assert classification.payload["mode"] == "interrupt"
        assert rt.router.classify_calls == 0
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_interrupt_pauses_paid_step_at_boundary(token: str) -> None:
    del token
    session = "stop-paid"
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(
                    action="paid_mock",
                    params={"_wait": True, "_change_id": "chg-paid", "_cost": 1},
                    expected="paid",
                    paid=True,
                    mutating=True,
                )
            ]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(session, "платный", project_id=13)
        await actions.started.wait()
        await rt.queue.accept(session, "стой")
        pending = [task for task in rt.queue._pending]
        if pending:
            import asyncio

            await asyncio.wait_for(asyncio.gather(*pending), timeout=2)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.executing.value
        actions.release.set()
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.paused.value
        assert rt.tasks.list_steps(task_id)[0]["status"] == "done"
        assert rt.tasks.list_steps(task_id)[0]["change_id"] == "chg-paid"
        checkpoint = rt.checkpoints.load_latest(task_id)
        assert checkpoint is not None
        assert "chg-paid" in checkpoint.change_ids
    finally:
        set_runtime(None)
