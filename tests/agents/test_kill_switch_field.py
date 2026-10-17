"""Поле MessageIn.kill_switch: тот же стоп, что фраза, даже при явном mode."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import MessageMode, PlanStep
from app.web.routers.orchestrator_chat import MessageIn, router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def _kills(runtime, session: str) -> list:
    return [event for event in runtime.bus.replay(session, 0) if event.type == "kill_switch"]


@pytest.mark.asyncio
async def test_kill_switch_field_overrides_append_and_stops_unpaid(token: str) -> None:
    del token
    actions = MockActions()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [PlanStep(action="slow", params={"_block": True, "_seconds": 30}, paid=False)]
        ),
    )
    set_runtime(runtime)
    session = "kill-field"
    try:
        assert MessageIn.model_validate({"text": "привет"}).kill_switch is False
        task_id = await runtime.supervisor.start_task(session, "долгий", project_id=13)
        await actions.started.wait()
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            quiet = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "просто текст", "mode": "append", "kill_switch": False},
                headers=_headers(),
            )
            assert quiet.status_code == 200
            await runtime.queue.drain()
            assert _kills(runtime, session) == []
            assert runtime.tasks.get(task_id)["status"] == "executing"

            omitted = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "ещё текст", "mode": "append"},
                headers=_headers(),
            )
            assert omitted.status_code == 200
            await runtime.queue.drain()
            assert _kills(runtime, session) == []

            stopped = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "останови генерацию", "mode": "append", "kill_switch": True},
                headers=_headers(),
            )
            assert stopped.status_code == 200
            assert stopped.json()["ok"] is True
            assert stopped.json()["session"] == session
            await runtime.queue.drain()

        events = runtime.bus.replay(session, 0)
        armed = [
            event
            for event in events
            if event.type == "classification" and event.payload.get("kill_switch") is True
        ]
        assert len(armed) == 1
        assert armed[0].payload["mode"] == MessageMode.interrupt.value
        kill = _kills(runtime, session)
        assert len(kill) == 1
        assert kill[0].payload["stop_key_set"] is False
        assert kill[0].payload["stop_key"] == "pending"
        assert kill[0].payload["only_node"] is True
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == "cancelled"
        assert [call["name"] for call in actions.calls] == ["slow"]

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            phrase = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "аварийный стоп", "mode": "append", "kill_switch": False},
                headers=_headers(),
            )
        assert phrase.status_code == 200
        await runtime.queue.drain()
        classifications = [event for event in runtime.bus.replay(session, 0) if event.type == "classification"]
        assert classifications[-1].payload["kill_switch"] is True
        assert classifications[-1].payload["mode"] == MessageMode.interrupt.value
        assert len(_kills(runtime, session)) == 2
        assert TEST_TOKEN not in stopped.text
    finally:
        set_runtime(None)
