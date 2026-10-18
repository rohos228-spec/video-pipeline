"""Контур A через HTTP: message → project → approve.

Роуты чата, не прямые ``start_task`` / ``grant_approval``.
Пустой ``decision`` — тот grant, который сервер принимает на риске после
привязки проекта. ``retry_step`` на этом ``change_id`` — зона #358/#367.
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

_SHOT = {"кадры": [{"id": 1, "активен": False}]}


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def _plan_text() -> str:
    steps = []
    for name in ("look", "mark_shot", "list", "charge", "save"):
        params: dict = {}
        if name == "mark_shot":
            params = {"кадры": [{"id": 1, "активен": False}]}
        elif name == "charge":
            params = {"_cost": 1}
        steps.append(
            {
                "action": name,
                "params": params,
                "expected": name,
                "paid": name == "charge",
                "mutating": name in {"mark_shot", "charge", "save"},
                "risk": "high" if name == "charge" else "none",
                "estimated_cost": 1 if name == "charge" else 0,
            }
        )
    return json.dumps({"steps": steps}, ensure_ascii=False)


def _risks() -> dict[str, str]:
    return {
        "look": "none",
        "mark_shot": "none",
        "list": "none",
        "save": "none",
        "charge": "high",
        "delete_child_frame": "high",
    }


def _runtime_with(script: list[str]):
    actions = MockActions(risks=_risks())
    router_llm = ScriptedRouter()
    router_llm.script = list(script)
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router_llm,
        planner=RouterPlanner(router_llm),
        critic=RouterCritic(router_llm),
    )
    return runtime, actions


def _approvals(runtime, session: str, task_id: int) -> list:
    return [
        event
        for event in runtime.bus.replay(session, 0)
        if event.type == "approval_required" and event.task_id == task_id
    ]


def _task_id(runtime, session: str) -> int:
    """Id задачи сессии. ``_runs`` живёт и после терминального статуса."""
    ids = [
        int(task_id)
        for task_id in runtime.supervisor._runs
        if (runtime.tasks.get(task_id) or {}).get("session_id") == session
    ]
    assert len(ids) == 1, ids
    return ids[0]


async def _settle(runtime, task_id: int) -> None:
    await runtime.queue.drain()
    run = runtime.supervisor._runs.get(task_id)
    if run is not None and not run.done():
        await runtime.supervisor.wait(task_id, timeout=5)


async def _read_sse_until(app: FastAPI, path: str, needle: bytes) -> bytes:
    """Один кусок SSE. Глобальный event sse-starlette привязан к loop теста."""
    AppStatus.should_exit_event = None
    body = bytearray()
    seen = asyncio.Event()
    first_request = True

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
        "headers": [(b"authorization", f"Bearer {TEST_TOKEN}".encode())],
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(seen.wait(), timeout=3)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        AppStatus.should_exit_event = None
    return bytes(body)


@pytest.mark.asyncio
async def test_contour_a_http_reaches_done_and_keeps_inactive_shot(token: str) -> None:
    """Пять шагов без project_id, затем привязка и пустые approve до done.

    ``активен: False`` остаётся в ``кадры[]`` вызова. Платный шаг — один раз.
    """
    del token
    plan = _plan_text()
    runtime, actions = _runtime_with([plan, plan, "approve"])
    set_runtime(runtime)
    session = "contour-a-http"
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "план шагов кадра", "mode": "task"},
                headers=_headers(),
            )
            assert created.status_code == 200
            assert created.json()["ok"] is True
            await runtime.queue.drain()
            task_id = _task_id(runtime, session)
            await _settle(runtime, task_id)
            row = runtime.tasks.get(task_id)
            assert row["status"] == TaskStatus.waiting_approval.value
            assert row["project_id"] is None
            assert runtime.supervisor._block_reason.get(task_id) == "missing_project_id"
            first = _approvals(runtime, session, task_id)
            assert len(first) == 1
            assert first[0].payload["reason"] == "missing_project_id"
            assert str(first[0].payload["change_id"]).startswith("orch-appr-")
            assert actions.calls == []
            assert float(row["spend_actions"] or 0) == 0

            bound = await client.post(
                f"/api/orchestrator/chat/{session}/project",
                json={"project_id": 13, "task_id": task_id},
                headers=_headers(),
            )
            assert bound.status_code == 200
            assert bound.json()["project_id"] == 13
            await _settle(runtime, task_id)
            row = runtime.tasks.get(task_id)
            assert row["project_id"] == 13
            assert row["status"] == TaskStatus.waiting_approval.value
            assert runtime.supervisor._block_reason.get(task_id) != "missing_project_id"
            held = _approvals(runtime, session, task_id)
            assert len(held) >= 2
            assert held[-1].payload["reason"] in {"risk", "paid"}
            second_id = str(held[-1].payload["change_id"])
            assert second_id.startswith("orch-appr-")
            assert second_id != str(first[0].payload["change_id"])
            assert [call["name"] for call in actions.calls] == ["look", "mark_shot", "list"]
            assert actions.calls[1]["params"]["кадры"] == _SHOT["кадры"]
            assert actions.calls[1]["params"]["кадры"][0]["активен"] is False
            assert "charge" not in [call["name"] for call in actions.calls]
            assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == 0

            for _ in range(4):
                if runtime.tasks.get(task_id)["status"] == TaskStatus.done.value:
                    break
                change_id = str(_approvals(runtime, session, task_id)[-1].payload["change_id"])
                assert change_id.startswith("orch-appr-")
                granted = await client.post(
                    f"/api/orchestrator/chat/{session}/approve",
                    json={"change_id": change_id},
                    headers=_headers(),
                )
                assert granted.status_code == 200, granted.text
                await _settle(runtime, task_id)
            else:
                raise AssertionError(runtime.tasks.get(task_id)["status"])

            assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
            names = [call["name"] for call in actions.calls]
            assert names.count("charge") == 1
            assert names.count("save") == 1
            assert names[-2:] == ["charge", "save"]
            for call in actions.calls:
                if call["name"] == "mark_shot":
                    assert call["params"]["кадры"] == [{"id": 1, "активен": False}]
            assert float(runtime.tasks.get(task_id)["spend_actions"] or 0) == 1.0
            charge_rows = [
                step for step in runtime.tasks.list_steps(task_id) if step["action_name"] == "charge"
            ]
            assert len(charge_rows) == 1
            assert charge_rows[0]["status"] == "done"
            assert float(charge_rows[0]["cost"] or 0) == 1.0
            decided = [
                event
                for event in runtime.bus.replay(session, 0)
                if event.type == "approval_decided" and event.task_id == task_id
            ]
            assert decided
            raw = await _read_sse_until(
                _app(),
                f"/api/orchestrator/chat/{session}/stream",
                b"approval_decided",
            )
            assert b"event: approval_decided" in raw
            assert TEST_TOKEN not in created.text
            assert TEST_TOKEN not in bound.text
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_contour_a_http_blocks_frame_deletion(token: str) -> None:
    """Синтетическое удаление кадра через POST message не исполняется."""
    del token
    plan = json.dumps(
        {
            "steps": [
                {
                    "action": "delete_child_frame",
                    "params": {},
                    "expected": "gone",
                    "paid": False,
                    "mutating": True,
                    "risk": "high",
                    "estimated_cost": 0,
                }
            ]
        }
    )
    runtime, actions = _runtime_with([plan])
    set_runtime(runtime)
    session = "contour-a-del"
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "удали кадр", "mode": "task", "project_id": 13},
                headers=_headers(),
            )
            assert created.status_code == 200
            await runtime.queue.drain()
            task_id = _task_id(runtime, session)
            await _settle(runtime, task_id)
        row = runtime.tasks.get(task_id)
        assert row["status"] == TaskStatus.failed.value
        assert row["error"] == "deletion_blocked"
        assert "delete_child_frame" not in [call["name"] for call in actions.calls]
        assert TEST_TOKEN not in created.text
    finally:
        set_runtime(None)
