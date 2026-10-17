"""A1: ядро само выдаёт change_id. Заглушки — контракт WS2 r2.1 (ed355997).

Схема ``docs/fable/action_results_contract.schema.json``, sha256
``e4eb29277f0d2128fcae5ec1006c7b95ba5522529e362129a90d39884c1fab9d``.
Пакет WS2 не импортируется: конверты — dict.
``ApprovalRequired`` без ``change_id``. ``EditResult`` — ``version`` и
``before`` или непустой ``snapshot_id``. ``SoftDeleteResult`` — ``marked``
и ссылка на снимок (``snapshot_id`` / ``archive_ref`` / ``snapshot_ref``).
``revert_node_to_git`` и правка без версии — destructive.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sse_starlette.sse import AppStatus

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import PlanStep, Risk, TaskStatus, metadata_call_gate
from app.web.routers.orchestrator_chat import router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter

_SCHEMA = "ws2-r2.1"


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def _meta(name: str, *, risk: str, read_only: bool, destructive: bool, paid: bool) -> dict[str, object]:
    return {
        "name": name,
        "risk": risk,
        "read_only": read_only,
        "destructive": destructive,
        "paid": paid,
    }


class _Registry:
    def __init__(self, table: dict[str, dict[str, object]], responder) -> None:
        self.table = table
        self.responder = responder
        self.calls: list[str] = []

    def metadata_of(self, name: str) -> dict[str, object]:
        if name not in self.table:
            raise KeyError(name)
        return self.table[name]

    def risk_of(self, name: str) -> Risk:
        return Risk(str(self.metadata_of(name)["risk"]))

    async def execute(self, name: str, params: dict, task_ctx: dict) -> dict:
        del params, task_ctx
        self.calls.append(name)
        return self.responder(name)


def _edit_ok(action: str, foreign: str) -> dict:
    return {
        "status": "ok",
        "result": {
            "action": action,
            "change_id": foreign,
            "node_ref": {"project_id": 13, "node_id": "n_scene_writer", "kind": "node"},
            "before": {"source": "db", "db_version": 4},
            "version": 5,
            "base_version": 4,
            "diff": {"unified": "--- a\n+++ b\n@@\n-db\n+git\n"},
            "open_url": "/api/projects/13",
            "reversible": True,
            "skipped": ["n_other"],
            "partial": True,
        },
        "error": None,
        "warnings": ["git file not written"],
    }


def _paid_ok(action: str, foreign: str) -> dict:
    return {
        "status": "ok",
        "result": {"action": action, "change_id": foreign, "skipped": ["take-2"], "partial": True},
        "error": None,
        "warnings": ["estimate was null"],
    }


def _soft_ok(action: str, foreign: str) -> dict:
    return {
        "status": "ok",
        "result": {
            "change_id": foreign,
            "action": action,
            "marked": True,
            "restore_action": "prompt.restore_master",
            "snapshot_id": "snap-13-6",
        },
        "error": None,
        "warnings": ["file left in place"],
    }


def _approval_required(action: str, meta: dict[str, object], foreign: str) -> dict:
    return {
        "status": "approval_required",
        "result": {
            "action": action,
            "change_id": foreign,
            "metadata": meta,
            "reason": "исполнитель просит подтверждение",
            "preview": {"change": "прочитать код узла", "estimate": 0},
        },
        "error": None,
        "warnings": ["schema has no change_id"],
    }


def test_metadata_gate_uses_r21_flags() -> None:
    table = {
        "revert_node_to_git": _meta(
            "revert_node_to_git",
            risk="medium",
            read_only=False,
            destructive=True,
            paid=False,
        ),
        "montage.suggest_scene_variants": _meta(
            "montage.suggest_scene_variants",
            risk="high",
            read_only=False,
            destructive=False,
            paid=True,
        ),
        "read_pipeline_code": _meta(
            "read_pipeline_code",
            risk="none",
            read_only=True,
            destructive=False,
            paid=False,
        ),
    }

    class _Reg:
        def metadata_of(self, name: str) -> dict[str, object]:
            if name == "missing":
                raise KeyError(name)
            return table[name]

    registry = _Reg()
    assert metadata_call_gate("missing", registry) == "unknown"
    assert metadata_call_gate("revert_node_to_git", registry) == "destructive"
    assert metadata_call_gate("montage.suggest_scene_variants", registry) == "paid"
    assert metadata_call_gate("read_pipeline_code", registry) is None
    assert metadata_call_gate("look", MockActions()) is None
    assert _SCHEMA == "ws2-r2.1"


async def _wait_status(runtime, task_id: int, status: str) -> None:
    for _ in range(100):
        if runtime.tasks.get(task_id)["status"] == status:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(runtime.tasks.get(task_id)["status"])


def _task_id(runtime, session: str) -> int:
    ids = [
        int(task_id)
        for task_id, _status in runtime.supervisor.history
        if (runtime.tasks.get(task_id) or {}).get("session_id") == session
    ]
    assert ids
    return ids[-1]


def _approvals(runtime, session: str) -> list:
    return [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"]


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "meta", "foreign", "responder"),
    [
        (
            "revert_node_to_git",
            _meta("revert_node_to_git", risk="medium", read_only=False, destructive=True, paid=False),
            "foreign-revert",
            _edit_ok,
        ),
        (
            "montage.suggest_scene_variants",
            _meta(
                "montage.suggest_scene_variants",
                risk="high",
                read_only=False,
                destructive=False,
                paid=True,
            ),
            "foreign-paid",
            _paid_ok,
        ),
    ],
)
async def test_http_paid_or_destructive_runs_once_after_approve(
    token: str,
    action: str,
    meta: dict[str, object],
    foreign: str,
    responder,
) -> None:
    """До approve вызовов 0, после — ровно 1. В шаге и SSE — ядровой change_id."""
    del token
    session = "a1-" + action.replace(".", "-").replace("_", "-")
    actions = _Registry({action: meta}, lambda name: responder(name, foreign))
    step = PlanStep(action=action, mutating=True, risk="none")
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([step]),
    )
    set_runtime(runtime)
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "правь", "mode": "task", "project_id": 13},
                headers=_headers(),
            )
            assert created.status_code == 200
            assert TEST_TOKEN not in created.text
            await runtime.queue.drain()
            task_id = _task_id(runtime, session)
            await _wait_status(runtime, task_id, TaskStatus.waiting_approval.value)
            assert actions.calls == []
            opened = _approvals(runtime, session)
            assert opened
            change_id = str(opened[-1].payload["change_id"])
            assert change_id.startswith(f"orch-appr-{task_id}-")
            assert change_id != foreign
            raw = await _read_sse(
                f"/api/orchestrator/chat/{session}/stream",
                b"event: approval_required",
            )
            assert b"approval_required" in raw
            assert change_id.encode() in raw
            assert foreign.encode() not in raw

            approved = await client.post(
                f"/api/orchestrator/chat/{session}/approve",
                json={"change_id": change_id},
                headers=_headers(),
            )
            assert approved.status_code == 200
            assert TEST_TOKEN not in approved.text
            await runtime.supervisor.wait(task_id, timeout=5)
            assert actions.calls == [action]
            assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
            row = runtime.tasks.list_steps(task_id)[-1]
            assert row["status"] == "done"
            assert row["change_id"] == change_id
            finished = [event for event in runtime.bus.replay(session, 0) if event.type == "step_finished"]
            assert finished[-1].payload["change_id"] == change_id
            assert foreign not in json.dumps(finished[-1].payload)
            assert finished[-1].payload["warnings"]
            done_raw = await _read_sse(
                f"/api/orchestrator/chat/{session}/stream",
                b"event: step_finished",
            )
            assert change_id.encode() in done_raw
            assert foreign.encode() not in done_raw
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_executor_approval_required_ignores_foreign_change_id(token: str) -> None:
    del token
    action = "read_pipeline_code"
    meta = _meta(action, risk="none", read_only=True, destructive=False, paid=False)
    foreign = "foreign-appr"
    actions = _Registry({action: meta}, lambda name: _approval_required(name, meta, foreign))
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action=action)]),
    )
    set_runtime(runtime)
    session = "a1-exec-appr"
    try:
        task_id = await runtime.supervisor.start_task(session, "прочитай", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == [action]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        opened = _approvals(runtime, session)
        assert opened[-1].payload["reason"] == "approval_required"
        change_id = str(opened[-1].payload["change_id"])
        assert change_id.startswith("orch-appr-")
        assert change_id != foreign
        assert "change_id" not in opened[-1].payload.get("result", {})
        row = runtime.tasks.list_steps(task_id)[-1]
        assert row["status"] == "waiting_approval"
        assert row["change_id"] == change_id
        assert foreign not in json.dumps(opened[-1].payload)
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_unapproved_ok_is_not_counted(token: str) -> None:
    del token
    action = "revert_node_to_git"
    meta = _meta(action, risk="none", read_only=False, destructive=True, paid=False)
    actions = _Registry({action: meta}, lambda name: _edit_ok(name, "foreign-lock"))
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([]),
    )
    set_runtime(runtime)
    session = "a1-lock"
    try:
        task_id = runtime.tasks.insert(session, "замок", TaskStatus.executing.value, project_id=13)
        step = PlanStep(action=action, mutating=True)
        step_id = runtime.tasks.insert_step(task_id, 0, step)
        accepted = await runtime.supervisor._record(
            task_id,
            session,
            step,
            step_id,
            _edit_ok(action, "foreign-lock"),
            0,
        )
        assert accepted is False
        assert actions.calls == []
        row = runtime.tasks.list_steps(task_id)[0]
        assert row["status"] == "unapproved_action"
        assert not row["change_id"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert runtime.tasks.get(task_id)["error"] == "unapproved_action"
        events = [event for event in runtime.bus.replay(session, 0) if event.type == "unapproved_action"]
        assert events[-1].payload["status"] == "unapproved_action"
        assert "foreign-lock" not in json.dumps(events[-1].payload)
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_unversioned_edit_is_not_done(token: str) -> None:
    del token
    action = "edit_loose"
    meta = _meta(action, risk="none", read_only=False, destructive=True, paid=False)

    def _loose(name: str) -> dict:
        return {
            "status": "ok",
            "result": {
                "action": name,
                "change_id": "foreign-loose",
                "node_ref": {"project_id": 13, "node_id": "n_x", "kind": "prompt"},
                "diff": {"unified": "+x\n"},
                "open_url": "/api/projects/13",
                "reversible": False,
            },
            "error": None,
            "warnings": ["no version"],
        }

    actions = _Registry({action: meta}, _loose)
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action=action, mutating=True, risk="none")]),
    )
    set_runtime(runtime)
    session = "a1-unversioned"
    try:
        task_id = await runtime.supervisor.start_task(session, "правь без версии", project_id=13)
        await _wait_status(runtime, task_id, TaskStatus.waiting_approval.value)
        assert actions.calls == []
        await runtime.supervisor.grant_approval(session)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == [action]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert runtime.tasks.get(task_id)["error"] == "unversioned_edit"
        row = runtime.tasks.list_steps(task_id)[-1]
        assert row["status"] == "unversioned_edit"
        assert row["change_id"] != "foreign-loose"
        assert not row["change_id"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_soft_delete_envelope_records_core_change_id(token: str) -> None:
    del token
    action = "prompt.retire_master"
    meta = _meta(action, risk="none", read_only=False, destructive=True, paid=False)
    foreign = "foreign-soft"
    actions = _Registry({action: meta}, lambda name: _soft_ok(name, foreign))
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action=action, mutating=True, risk="none")]),
    )
    set_runtime(runtime)
    session = "a1-soft"
    try:
        task_id = await runtime.supervisor.start_task(session, "пометь", project_id=13)
        await _wait_status(runtime, task_id, TaskStatus.waiting_approval.value)
        assert actions.calls == []
        change_id = str(_approvals(runtime, session)[-1].payload["change_id"])
        await runtime.supervisor.grant_approval(session, change_id=change_id)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == [action]
        row = runtime.tasks.list_steps(task_id)[-1]
        assert row["status"] == "done"
        assert row["change_id"] == change_id
        assert row["change_id"] != foreign
        finished = [event for event in runtime.bus.replay(session, 0) if event.type == "step_finished"]
        assert finished[-1].payload["change_id"] == change_id
        assert finished[-1].payload["warnings"] == ["file left in place"]
    finally:
        set_runtime(None)
