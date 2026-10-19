"""approve partial ждёт, пока inner write закончится."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import ApprovalDecision, PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


@pytest.mark.asyncio
async def test_accept_partial_waits_until_the_inner_write_finishes(token: str) -> None:
    """approve partial не продолжает план, пока inner write ещё выполняется."""
    del token
    actions = MockActions()
    approve: asyncio.Task[object] | None = None
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="write", params={"_wait": True}, mutating=True, paid=False),
                PlanStep(action="look", risk="none", expected="see"),
            ]
        ),
    )
    set_runtime(runtime)
    session = "partial-inflight"
    try:
        task_id = await runtime.supervisor.start_task(session, "рви", project_id=13)
        await actions.started.wait()
        published = runtime.supervisor.request_kill(session)
        if published is not None:
            await published
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        approve = asyncio.create_task(runtime.supervisor.grant_approval(session, ApprovalDecision.approve))
        await asyncio.sleep(0.05)
        assert [call["name"] for call in actions.calls] == ["write"]
        assert not approve.done()
        actions.release.set()
        await approve
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["write", "look"]
    finally:
        actions.release.set()
        if approve is not None and not approve.done():
            approve.cancel()
        set_runtime(None)
