"""HTTP посреди платного шага: steer, append и обрыв SSE.

Задача уже ``executing``. Сообщения идут через роут чата. Поток рвётся
и поднимается снова с ``Last-Event-ID``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sse_starlette.sse import AppStatus

from app.orchestrator.agents.critic import RouterCritic
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import AgentRole, TaskStatus
from app.web.routers.orchestrator_chat import router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter

_APPEND = "пометка альфа-кадр для планировщика"


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def _step(name: str, *, paid: bool = False, params: dict | None = None) -> dict:
    return {
        "action": name,
        "params": dict(params or {}),
        "expected": name,
        "paid": paid,
        "mutating": True,
        "risk": "none",
        "estimated_cost": 1 if paid else 0,
    }


def _plan(*steps: dict) -> str:
    return json.dumps({"steps": list(steps)}, ensure_ascii=False)


def _parse_sse(raw: bytes) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    text = raw.decode().replace("\r\n", "\n").replace("\r", "\n")
    for block in text.split("\n\n"):
        event_id: int | None = None
        name = ""
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("id:"):
                event_id = int(line.split(":", 1)[1].strip())
            elif line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
        if event_id is not None:
            found.append({"id": event_id, "event": name})
    return found


async def _read_sse(path: str, needle: bytes, last_event_id: int | None) -> bytes:
    AppStatus.should_exit_event = None
    body = bytearray()
    seen = asyncio.Event()
    first_request = True
    headers = [(b"authorization", f"Bearer {TEST_TOKEN}".encode())]
    if last_event_id is not None:
        headers.append((b"last-event-id", str(last_event_id).encode()))

    async def receive() -> dict[str, object]:
        nonlocal first_request
        if first_request:
            first_request = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Future()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start" and message.get("status") != 200:
            raise AssertionError(message.get("status"))
        if message["type"] == "http.response.body":
            body.extend(message.get("body") or b"")
            if needle in body:
                seen.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    task = asyncio.create_task(_app()(scope, receive, send))
    try:
        await asyncio.wait_for(seen.wait(), timeout=5)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        AppStatus.should_exit_event = None
    return bytes(body)


def _task_id(runtime, session: str) -> int:
    ids = [
        int(task_id)
        for task_id in runtime.supervisor._runs
        if (runtime.tasks.get(task_id) or {}).get("session_id") == session
    ]
    assert len(ids) == 1, ids
    return ids[0]


def _planner_texts(router_llm: ScriptedRouter) -> list[str]:
    texts: list[str] = []
    for call, messages in zip(router_llm.seen_calls, router_llm.seen_messages, strict=True):
        if call.get("agent_role") != AgentRole.planner.value:
            continue
        texts.extend(str(item.get("content") or "") for item in messages)
    return texts


@pytest.mark.asyncio
async def test_midtask_steer_append_and_sse_reconnect(token: str) -> None:
    """Платный шаг один раз. Steer перестраивает план, append виден планировщику.

    Обрыв SSE и ``Last-Event-ID`` не теряют события и не повторяют id.
    """
    del token
    first = _plan(
        _step("charge", paid=True, params={"_cost": 1, "_wait": True}),
        _step("save"),
    )
    revised = _plan(
        _step("charge", paid=True, params={"_cost": 1}),
        _step("note"),
    )
    actions = MockActions(risks={"charge": "none", "save": "none", "note": "none"})
    router_llm = ScriptedRouter()
    router_llm.script = [first, "approve", "принял пометку", revised, "approve"]
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router_llm,
        planner=RouterPlanner(router_llm),
        critic=RouterCritic(router_llm),
    )
    set_runtime(runtime)
    session = "mid-task-http"
    path = f"/api/orchestrator/chat/{session}/stream"
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "план шагов", "mode": "task", "project_id": 13},
                headers=_headers(),
            )
            assert created.status_code == 200
            await runtime.queue.drain()
            task_id = _task_id(runtime, session)
            await actions.started.wait()
            assert runtime.tasks.get(task_id)["status"] == TaskStatus.executing.value
            assert [call["name"] for call in actions.calls] == ["charge"]

            first_raw = await _read_sse(path, b"event: step_started", None)
            first_events = _parse_sse(first_raw)
            assert first_events
            first_ids = [int(item["id"]) for item in first_events]
            assert first_ids == sorted(first_ids)
            assert len(first_ids) == len(set(first_ids))
            last_id = first_ids[-1]
            plan_before = runtime.tasks.get(task_id)["plan_json"]

            appended = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": _APPEND, "mode": "append"},
                headers=_headers(),
            )
            assert appended.status_code == 200
            await runtime.queue.drain()
            steered = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "измени план", "mode": "steer"},
                headers=_headers(),
            )
            assert steered.status_code == 200
            await runtime.queue.drain()
            assert runtime.tasks.get(task_id)["status"] == TaskStatus.executing.value
            assert [call["name"] for call in actions.calls] == ["charge"]

            actions.release.set()
            run = runtime.supervisor._runs.get(task_id)
            assert run is not None
            await runtime.supervisor.wait(task_id, timeout=5)

        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        names = [call["name"] for call in actions.calls]
        assert names.count("charge") == 1
        assert "note" in names
        charge_rows = [step for step in runtime.tasks.list_steps(task_id) if step["action_name"] == "charge"]
        assert len(charge_rows) == 1
        assert charge_rows[0]["status"] == "done"
        assert float(charge_rows[0]["cost"] or 0) == 1.0
        assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == 1.0
        assert runtime.tasks.get(task_id)["plan_json"] != plan_before
        planning = [
            event
            for event in runtime.bus.replay(session, 0)
            if event.type == "agent_status" and event.payload.get("status") == TaskStatus.planning.value
        ]
        assert len(planning) >= 2
        assert any(_APPEND in text for text in _planner_texts(router_llm))

        second_raw = await _read_sse(path, b"event: task_done", last_id)
        second_events = _parse_sse(second_raw)
        second_ids = [int(item["id"]) for item in second_events]
        assert second_ids == sorted(second_ids)
        assert len(second_ids) == len(set(second_ids))
        assert second_ids[0] > last_id
        assert set(first_ids).isdisjoint(second_ids)
        full_ids = [event.seq for event in runtime.bus.replay(session, 0)]
        assert full_ids == sorted(full_ids)
        assert len(full_ids) == len(set(full_ids))
        assert sorted(set(first_ids) | set(second_ids)) == full_ids
        assert TEST_TOKEN not in created.text
        assert TEST_TOKEN not in appended.text
        assert TEST_TOKEN not in steered.text
    finally:
        set_runtime(None)
