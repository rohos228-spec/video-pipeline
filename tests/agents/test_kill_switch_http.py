"""HTTP kill switch: платный шаг, planning, рестарт и чужая сессия.

Kill — ``POST .../message`` с ``kill_switch: true``. Resume — ``POST .../resume``.
Платный шаг в полёте остаётся ``waiting_approval`` / ``paid_outcome_unknown``.
Planning без шага уходит в терминальный ``cancelled``. Чекпоинта нет, поэтому
resume отвечает 404 (LookupError), а не поднимает задачу.
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
from app.orchestrator.contracts import TaskStatus
from app.web.routers.orchestrator_chat import router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def _plan(*names: str, paid: str | None = None, wait: str | None = None) -> str:
    steps = []
    for name in names:
        params: dict[str, object] = {}
        if name == paid:
            params["_cost"] = 1
        if name == wait:
            params["_wait"] = True
        steps.append(
            {
                "action": name,
                "params": params,
                "expected": name,
                "paid": name == paid,
                "mutating": True,
                "risk": "none",
                "estimated_cost": 1 if name == paid else 0,
            }
        )
    return json.dumps({"steps": steps}, ensure_ascii=False)


def _parse_sse(raw: bytes) -> list[str]:
    text = raw.decode().replace("\r\n", "\n").replace("\r", "\n")
    names: list[str] = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("event:"):
                names.append(line.split(":", 1)[1].strip())
    return names


async def _read_sse(path: str, needle: bytes) -> bytes:
    AppStatus.should_exit_event = None
    body = bytearray()
    seen = asyncio.Event()
    first = True

    async def receive() -> dict[str, object]:
        nonlocal first
        if first:
            first = False
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
        "headers": [(b"authorization", f"Bearer {TEST_TOKEN}".encode())],
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


def _charge_rows(runtime, task_id: int) -> list[dict]:
    return [step for step in runtime.tasks.list_steps(task_id) if step["action_name"] == "charge"]


async def _kill(client: AsyncClient, session: str):
    return await client.post(
        f"/api/orchestrator/chat/{session}/message",
        json={"text": "останови генерацию", "mode": "append", "kill_switch": True},
        headers=_headers(),
    )


@pytest.mark.asyncio
async def test_kill_during_paid_step_keeps_change_id_and_resume_skips_replan(token: str) -> None:
    """Kill в executing не повторяет оплату. Resume после рестарта не планирует заново."""
    del token
    actions = MockActions(risks={"charge": "none", "save": "none"})
    router_llm = ScriptedRouter()
    router_llm.script = [_plan("charge", "save", paid="charge", wait="charge"), "approve"]
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router_llm,
        planner=RouterPlanner(router_llm),
        critic=RouterCritic(router_llm),
    )
    set_runtime(runtime)
    session = "kill-paid"
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
            plan_at_kill = runtime.tasks.get(task_id)["plan_json"]

            stopped = await _kill(client, session)
            assert stopped.status_code == 200
            await runtime.queue.drain()
            raw = await _read_sse(f"/api/orchestrator/chat/{session}/stream", b"event: kill_switch")
            assert "kill_switch" in _parse_sse(raw)

            actions.release.set()
            await runtime.supervisor.wait(task_id, timeout=5)

        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert runtime.supervisor._block_reason.get(task_id) == "paid_outcome_unknown"
        assert [call["name"] for call in actions.calls] == ["charge"]
        assert "save" not in [call["name"] for call in actions.calls]
        assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == 1.0
        rows = _charge_rows(runtime, task_id)
        assert len(rows) == 1
        change_id = str(rows[0]["change_id"] or "")
        assert change_id
        checkpoint = runtime.checkpoints.load_latest(task_id)
        assert checkpoint is not None
        assert change_id in checkpoint.change_ids
        assert runtime.tasks.get(task_id)["plan_json"] == plan_at_kill

        fresh = build_runtime(
            actions=actions,
            validators=MockValidators(),
            router=router_llm,
            planner=RouterPlanner(router_llm),
            critic=RouterCritic(router_llm),
        )
        set_runtime(fresh)
        await fresh.supervisor.recover_after_restart()
        assert fresh.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert fresh.tasks.get(task_id)["plan_json"] == plan_at_kill
        planning_before = [
            event
            for event in fresh.bus.replay(session, 0)
            if event.type == "agent_status" and event.payload.get("status") == TaskStatus.planning.value
        ]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resumed = await client.post(
                f"/api/orchestrator/chat/{session}/resume",
                headers=_headers(),
            )
            assert resumed.status_code == 200
            assert TEST_TOKEN not in resumed.text
        await fresh.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["charge"]
        assert float(fresh.tasks.get(task_id)["spend_actions"] or 0) == 1.0
        assert fresh.tasks.get(task_id)["plan_json"] == plan_at_kill
        assert str(_charge_rows(fresh, task_id)[0]["change_id"]) == change_id
        planning_after = [
            event
            for event in fresh.bus.replay(session, 0)
            if event.type == "agent_status" and event.payload.get("status") == TaskStatus.planning.value
        ]
        assert planning_after == planning_before
        assert "save" not in [call["name"] for call in actions.calls]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_during_planning_cancels_without_payment(token: str) -> None:
    """Kill в planning: шагов и оплат нет. cancelled без чекпоинта не резюмируется."""
    del token
    actions = MockActions(risks={"charge": "none"})
    router_llm = ScriptedRouter()
    router_llm.call_delay = 30
    router_llm.script = [_plan("charge", paid="charge"), "approve"]
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router_llm,
        planner=RouterPlanner(router_llm),
        critic=RouterCritic(router_llm),
    )
    set_runtime(runtime)
    session = "kill-plan"
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "план шагов", "mode": "task", "project_id": 13},
                headers=_headers(),
            )
            assert created.status_code == 200
            await router_llm.call_started.wait()
            task_id = _task_id(runtime, session)
            assert runtime.tasks.get(task_id)["status"] == TaskStatus.planning.value
            stopped = await _kill(client, session)
            assert stopped.status_code == 200
            await runtime.queue.drain()
            await runtime.supervisor.wait(task_id, timeout=5)
            raw = await _read_sse(f"/api/orchestrator/chat/{session}/stream", b"event: kill_switch")
            assert "kill_switch" in _parse_sse(raw)
            refused = await client.post(
                f"/api/orchestrator/chat/{session}/resume",
                headers=_headers(),
            )
            assert refused.status_code == 404
            assert refused.json()["detail"] == "not found"
            assert TEST_TOKEN not in stopped.text
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.cancelled.value
        assert actions.calls == []
        assert runtime.tasks.list_steps(task_id) == []
        assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == 0
    finally:
        set_runtime(None)


async def _until(check, timeout: float = 5) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not check():
        if loop.time() >= deadline:
            raise AssertionError("condition")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_kill_one_session_does_not_stop_the_other(token: str) -> None:
    """Kill первой сессии не трогает статус, шаги и оплаты второй."""
    del token
    actions = MockActions(risks={"charge": "none", "tail": "none", "look": "none", "save": "none"})
    save_gate = asyncio.Event()

    async def _hold_save(name: str, params: dict, ctx: dict) -> None:
        del params, ctx
        if name == "save":
            await save_gate.wait()

    actions.on_execute = _hold_save
    router_llm = ScriptedRouter()
    router_llm.script = [
        _plan("charge", "tail", paid="charge", wait="charge"),
        "approve",
        _plan("look", "save"),
        "approve",
    ]
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router_llm,
        planner=RouterPlanner(router_llm),
        critic=RouterCritic(router_llm),
    )
    set_runtime(runtime)
    first = "kill-a"
    second = "kill-b"
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            opened = await client.post(
                f"/api/orchestrator/chat/{first}/message",
                json={"text": "план шагов", "mode": "task", "project_id": 13},
                headers=_headers(),
            )
            assert opened.status_code == 200
            await runtime.queue.drain()
            paid_id = _task_id(runtime, first)
            await asyncio.wait_for(actions.started.wait(), 5)
            assert runtime.tasks.get(paid_id)["status"] == TaskStatus.executing.value
            other = await client.post(
                f"/api/orchestrator/chat/{second}/message",
                json={"text": "план шагов", "mode": "task", "project_id": 13},
                headers=_headers(),
            )
            assert other.status_code == 200
            await runtime.queue.drain()
            other_id = _task_id(runtime, second)
            await _until(lambda: "save" in [call["name"] for call in actions.calls])
            assert runtime.tasks.get(other_id)["status"] == TaskStatus.executing.value

            stopped = await _kill(client, first)
            assert stopped.status_code == 200
            await runtime.queue.drain()
            raw_a = await _read_sse(f"/api/orchestrator/chat/{first}/stream", b"event: kill_switch")
            assert "kill_switch" in _parse_sse(raw_a)
            assert runtime.tasks.get(other_id)["status"] == TaskStatus.executing.value
            assert float(runtime.tasks.get(other_id)["spend_actions"] or 0) == 0
            other_steps = runtime.tasks.list_steps(other_id)
            assert [step["action_name"] for step in other_steps] == ["look", "save"]

            save_gate.set()
            await runtime.supervisor.wait(other_id, timeout=5)
            raw_b = await _read_sse(f"/api/orchestrator/chat/{second}/stream", b"event: task_done")
            assert "kill_switch" not in _parse_sse(raw_b)
            assert runtime.tasks.get(other_id)["status"] == TaskStatus.done.value
            assert [step["action_name"] for step in runtime.tasks.list_steps(other_id)] == ["look", "save"]
            assert float(runtime.tasks.get(other_id)["spend_actions"] or 0) == 0
            other_events = [event.type for event in runtime.bus.replay(second, 0)]
            assert "kill_switch" not in other_events

            actions.release.set()
            await runtime.supervisor.wait(paid_id, timeout=5)
            assert runtime.tasks.get(paid_id)["status"] == TaskStatus.waiting_approval.value
            assert [call["name"] for call in actions.calls].count("charge") == 1
            assert "tail" not in [call["name"] for call in actions.calls]
            assert float(runtime.tasks.get(paid_id)["spend_actions"] or 0) == 1.0
            assert runtime.tasks.get(other_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)
