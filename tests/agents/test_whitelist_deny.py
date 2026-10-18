"""Имя вне белого списка нельзя одобрить и нельзя выполнить."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.supervisor import ApprovalGrantError
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import (
    ACTION_DENYLIST,
    CheckpointData,
    PlanStep,
    TaskStatus,
    canonical_action_name,
    not_in_whitelist,
)
from app.web.routers.orchestrator_chat import router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter

_FORBIDDEN = ("exec", "edit_files", "fix_bugs", "edit_file", "bash", "frobnicate")


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


class _ClosedRegistry:
    """Закрытый реестр: неизвестное имя — KeyError, известное может притвориться безопасным."""

    def __init__(self, safe: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.safe = safe or set()

    def metadata_of(self, name: str) -> dict[str, object]:
        if name not in self.safe:
            raise KeyError(name)
        return {
            "name": name,
            "risk": "none",
            "read_only": True,
            "destructive": False,
            "paid": False,
        }

    async def execute(self, name: str, params: dict, task_ctx: dict) -> dict:
        del params, task_ctx
        self.calls.append(name)
        return {"ok": True, "data": {}, "change_id": None, "cost": 0.0}


def test_denylist_normalizes_and_at_sign_is_refused() -> None:
    registry = _ClosedRegistry(safe={"bash", "create_node", "create_node@new_group"})
    assert not_in_whitelist(" tools.Bash ", registry) is True
    assert not_in_whitelist("create_node@new_group", registry) is True
    assert not_in_whitelist("create_node", registry) is False


def _registry_for(action: str) -> _ClosedRegistry:
    """Denylist остаётся запретом, даже если metadata называет имя безопасным."""
    if canonical_action_name(action) in ACTION_DENYLIST:
        return _ClosedRegistry(safe={action})
    return _ClosedRegistry()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", _FORBIDDEN)
async def test_forbidden_action_is_not_executed(token: str, action: str) -> None:
    del token
    actions = _registry_for(action)
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action=action, risk="none")]),
    )
    set_runtime(runtime)
    session = f"wl-{action.replace('_', '-')}"
    try:
        task_id = await runtime.supervisor.start_task(session, "сделай", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        row = runtime.tasks.get(task_id)
        assert row["status"] == TaskStatus.failed.value
        assert row["error"] == "not_in_whitelist"
        assert actions.calls == []
        assert float(row["spend_actions"] or 0) == 0.0
        assert [event for event in runtime.bus.replay(session, 0) if event.type == "approval_required"] == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", _FORBIDDEN)
async def test_grant_refuses_forbidden_action(token: str, action: str) -> None:
    del token
    actions = _registry_for(action)
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([]),
    )
    set_runtime(runtime)
    session = f"grant-{action.replace('_', '-')}"
    try:
        task_id = runtime.tasks.insert(session, "цель", TaskStatus.waiting_approval.value, project_id=13)
        runtime.checkpoints.save(
            task_id,
            session,
            CheckpointData(plan=[PlanStep(action=action, risk="high")], position=0),
        )
        runtime.supervisor._session_task[session] = task_id
        runtime.supervisor._block_reason[task_id] = "risk"
        with pytest.raises(ApprovalGrantError) as caught:
            await runtime.supervisor.grant_approval(session)
        assert caught.value.status_code == 403
        assert caught.value.detail == "not_in_whitelist"
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", _FORBIDDEN)
async def test_http_forbidden_action_rejected_at_approve_and_run(token: str, action: str) -> None:
    del token
    actions = _registry_for(action)
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action=action, risk="none")]),
    )
    set_runtime(runtime)
    session = f"http-{action.replace('_', '-')}"
    try:
        transport = ASGITransport(app=_app())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            posted = await client.post(
                f"/api/orchestrator/chat/{session}/message",
                json={"text": "сделай", "mode": "task", "project_id": 13},
                headers=_headers(),
            )
            assert posted.status_code == 200
            await runtime.queue.drain()
            task_id = runtime.supervisor.active_task(session)
            assert task_id is not None
            await runtime.supervisor.wait(task_id, timeout=5)
            row = runtime.tasks.get(task_id)
            assert row["error"] == "not_in_whitelist"
            assert actions.calls == []
            refused = await client.post(
                f"/api/orchestrator/chat/{session}/approve",
                json={"decision": "approve"},
                headers=_headers(),
            )
            assert refused.status_code == 409
            assert actions.calls == []

            grant_session = f"http-grant-{action.replace('_', '-')}"
            held = runtime.tasks.insert(
                grant_session,
                "цель",
                TaskStatus.waiting_approval.value,
                project_id=13,
            )
            runtime.checkpoints.save(
                held,
                grant_session,
                CheckpointData(plan=[PlanStep(action=action, risk="high")], position=0),
            )
            runtime.supervisor._session_task[grant_session] = held
            runtime.supervisor._block_reason[held] = "risk"
            denied = await client.post(
                f"/api/orchestrator/chat/{grant_session}/approve",
                json={"decision": "approve"},
                headers=_headers(),
            )
            assert denied.status_code == 403
            assert denied.json()["detail"] == "not_in_whitelist"
            assert actions.calls == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", _FORBIDDEN)
async def test_execute_refuses_denylist_after_risk_grant(token: str, action: str) -> None:
    del token
    actions = _registry_for(action)
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([]),
    )
    set_runtime(runtime)
    session = "wl-execute"
    try:
        task_id = runtime.tasks.insert(session, "цель", TaskStatus.executing.value, project_id=13)
        step = PlanStep(action=action, risk="high")
        runtime.supervisor._risk_granted.add(runtime.supervisor._risk_grant_key(task_id, 0, step))
        await runtime.supervisor._execute_plan(task_id, session, [step], 0, "цель")
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["error"] == "not_in_whitelist"
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
class _BareRegistry:
    """Реестр без metadata_of и без тестового флага. Любое имя — отказ."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, name: str, params: dict, task_ctx: dict) -> dict:
        del params, task_ctx
        self.calls.append(name)
        return {"ok": True, "data": {}, "cost": 0.0}


@pytest.mark.asyncio
async def test_open_mock_without_metadata_is_refused(token: str) -> None:
    del token
    actions = _BareRegistry()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="look", risk="none")]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("wl-open", "смотри")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert runtime.tasks.get(task_id)["error"] == "not_in_whitelist"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_flagged_mock_still_runs_look(token: str) -> None:
    del token
    actions = MockActions()
    assert actions.allow_unlisted is True
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="look", risk="none")]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("wl-look", "смотри")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["look"]
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)
