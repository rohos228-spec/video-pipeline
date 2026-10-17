"""Ack уходит до классификации и не блокирует задачу."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep, TaskStatus
from app.orchestrator.events import persisted_types
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


@pytest.mark.asyncio
async def test_ack_before_classification_and_under_two_seconds(runtime) -> None:
    runtime.router.gate = asyncio.Event()
    runtime.router.answer = "ответ рядом"
    started = time.perf_counter()
    await runtime.queue.accept("ack-1", "сделай что-нибудь")
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0
    seen = runtime.bus.replay("ack-1", 0)
    assert [event.type for event in seen] == ["ack"]
    assert runtime.router.classify_calls == 0

    await asyncio.sleep(0)
    assert runtime.router.started.is_set()
    assert runtime.router.classify_calls == 1
    assert all(event.type != "classification" for event in runtime.bus.replay("ack-1", 0))

    runtime.router.gate.set()
    pending = list(runtime.queue._pending)
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending), timeout=2)
    types = [event.type for event in runtime.bus.replay("ack-1", 0)]
    assert types[0] == "ack"
    assert "classification" in types
    classification = next(event for event in runtime.bus.replay("ack-1", 0) if event.type == "classification")
    assert classification.payload["mode"] == "append"
    assert classification.payload["deterministic"] is False
    assert classification.seq > seen[0].seq
    text = "".join(
        event.payload.get("text", "")
        for event in runtime.bus.replay("ack-1", 0)
        if event.type == "token"
    )
    assert text == "ответ рядом"
    assert "token" not in persisted_types("ack-1")
    assert "ack" in persisted_types("ack-1")


@pytest.mark.asyncio
async def test_stop_word_is_deterministic_interrupt(runtime) -> None:
    await runtime.queue.accept("ack-2", "отмена")
    await asyncio.sleep(0)
    pending = list(runtime.queue._pending)
    if pending:
        await asyncio.wait_for(asyncio.gather(*pending), timeout=2)
    assert runtime.router.classify_calls == 0
    events = runtime.bus.replay("ack-2", 0)
    ack = next(event for event in events if event.type == "ack")
    classification = next(event for event in events if event.type == "classification")
    assert ack.seq < classification.seq
    assert classification.payload["mode"] == "interrupt"
    assert classification.payload["deterministic"] is True


@pytest.mark.asyncio
async def test_question_without_task_streams_without_touching_plan(token: str) -> None:
    del token
    actions = MockActions()
    router = ScriptedRouter()
    router.answer = "полдень"
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router,
        planner=FixedPlanner(
            [PlanStep(action="tick", params={"_tick": 8, "_dt": 0.02}, paid=False, mutating=False)]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ack-3", "долгая")
        while actions.ticks < 1:
            await asyncio.sleep(0.01)
        await rt.queue.accept("ack-3", "который час?")
        pending = list(rt.queue._pending)
        if pending:
            await asyncio.wait_for(asyncio.gather(*pending), timeout=2)
        assert router.classify_calls == 0
        assert rt.tasks.get(task_id)["status"] == TaskStatus.executing.value
        text = "".join(
            event.payload.get("text", "")
            for event in rt.bus.replay("ack-3", 0)
            if event.type == "token"
        )
        assert text == "полдень"
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert actions.calls[0]["name"] == "tick"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_running_task_is_not_blocked_by_classifier(token: str) -> None:
    del token
    actions = MockActions()
    router = ScriptedRouter()
    router.gate = asyncio.Event()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router,
        planner=FixedPlanner(
            [PlanStep(action="tick", params={"_tick": 20, "_dt": 0.02}, paid=False, mutating=False)]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("ack-4", "долгая")
        while actions.ticks < 1:
            await asyncio.sleep(0.01)
        await rt.queue.accept("ack-4", "сделай что-нибудь")
        await router.started.wait()
        marked = actions.ticks
        await asyncio.sleep(0.08)
        assert actions.ticks > marked
        assert rt.tasks.get(task_id)["status"] == TaskStatus.executing.value
        assert all(event.type != "classification" for event in rt.bus.replay("ack-4", 0))
        router.gate.set()
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)
