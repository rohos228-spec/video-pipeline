"""Append во время executing попадает в контекст следующего planner."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.agents.critic import RouterCritic
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import AgentRole, TaskStatus
from app.web.routers.orchestrator_chat import router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter

_NOTE = "пометка альфа-кадр для планировщика"


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


def _plan(second: str) -> str:
    return json.dumps(
        {
            "steps": [
                {
                    "action": "charge",
                    "params": {"_cost": 1, "_wait": True},
                    "expected": "pay",
                    "paid": True,
                    "mutating": True,
                    "risk": "none",
                    "estimated_cost": 1,
                },
                {
                    "action": second,
                    "params": {},
                    "expected": second,
                    "paid": False,
                    "mutating": True,
                    "risk": "none",
                    "estimated_cost": 0,
                },
            ]
        },
        ensure_ascii=False,
    )


def _planner_user(router_llm: ScriptedRouter) -> list[str]:
    texts: list[str] = []
    for call, messages in zip(router_llm.seen_calls, router_llm.seen_messages, strict=True):
        if call.get("agent_role") != AgentRole.planner.value:
            continue
        texts.extend(str(item.get("content") or "") for item in messages if item.get("role") == "user")
    return texts


@pytest.mark.asyncio
async def test_append_during_paid_step_reaches_next_planner(token: str) -> None:
    """План не меняется в момент append. Steer после него видит текст реплики."""
    del token
    actions = MockActions(risks={"charge": "none", "save": "none", "note": "none"})
    router_llm = ScriptedRouter()
    router_llm.script = [_plan("save"), "approve", "ок", _plan("note"), "approve"]
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router_llm,
        planner=RouterPlanner(router_llm),
        critic=RouterCritic(router_llm),
    )
    set_runtime(runtime)
    session = "append-ctx"
    headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "план шагов", "mode": "task", "project_id": 13},
                headers=headers,
            )
            assert created.status_code == 200
            await runtime.queue.drain()
            await actions.started.wait()
            task_id = next(iter(runtime.supervisor._runs))
            held = runtime.tasks.get(task_id)["plan_json"]
            assert runtime.tasks.get(task_id)["status"] == TaskStatus.executing.value
            appended = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": _NOTE, "mode": "append"},
                headers=headers,
            )
            assert appended.status_code == 200
            await runtime.queue.drain()
            assert runtime.tasks.get(task_id)["plan_json"] == held
            assert [call["name"] for call in actions.calls] == ["charge"]
            steered = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "измени план", "mode": "steer"},
                headers=headers,
            )
            assert steered.status_code == 200
            await runtime.queue.drain()
            actions.release.set()
            await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        users = _planner_user(router_llm)
        assert users
        assert _NOTE not in users[0]
        assert any(_NOTE in text for text in users[1:])
        assert [call["name"] for call in actions.calls].count("charge") == 1
        assert TEST_TOKEN not in appended.text
    finally:
        set_runtime(None)
