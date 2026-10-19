"""Имя вне белого списка нельзя одобрить и нельзя выполнить."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.supervisor import ApprovalGrantError, InvalidTransition
from app.orchestrator.agents.wiring import ActionsBridge
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.checkpoints import CheckpointStore
from app.orchestrator.contracts import (
    ACTION_DENYLIST,
    CheckpointData,
    PlanStep,
    TaskStatus,
    action_name_rejected,
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

    def metadata_registry(self) -> dict[str, dict[str, object]]:
        return {
            name: {
                "name": name,
                "risk": "none",
                "read_only": True,
                "destructive": False,
                "paid": False,
            }
            for name in self.safe
        }

    def metadata_of(self, name: str) -> dict[str, object]:
        listed = self.metadata_registry()
        if name not in listed:
            raise KeyError(name)
        return listed[name]

    async def execute(self, name: str, params: dict, task_ctx: dict) -> dict:
        del params, task_ctx
        self.calls.append(name)
        return {"ok": True, "data": {}, "change_id": None, "cost": 0.0}


def test_denylist_normalizes_and_at_sign_is_refused() -> None:
    registry = _ClosedRegistry(safe={"bash", "create_node", "create_node@new_group"})
    assert not_in_whitelist(" tools.Bash ", registry) is True
    assert not_in_whitelist("create_node@new_group", registry) is True
    assert not_in_whitelist("create_node", registry) is False
    assert not_in_whitelist("Create-Node", registry) is True
    assert not_in_whitelist("test.look", _ClosedRegistry(safe={"test.look"})) is False
    assert action_name_rejected("scene.deleteAll") is False
    assert not_in_whitelist("scene.deleteAll", MockActions()) is False


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
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert runtime.tasks.get(task_id)["error"] == "not_in_whitelist"
        assert runtime.supervisor.resume_allowed(task_id) is False
        assert runtime.supervisor.session_snapshot(session)["resume_allowed"] is False
        with pytest.raises(InvalidTransition):
            await runtime.supervisor.resume(task_id)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
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
            assert runtime.tasks.get(held)["status"] == TaskStatus.failed.value
            assert actions.calls == []
            resumed = await client.post(
                f"/api/orchestrator/chat/{grant_session}/resume",
                headers=_headers(),
            )
            listed = await client.get(
                f"/api/orchestrator/chat/{grant_session}/status",
                headers=_headers(),
            )
            assert resumed.status_code == 409
            assert listed.status_code == 200
            assert listed.json()["resume_allowed"] is False
            assert listed.json()["status"] == TaskStatus.failed.value
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


class _Ws2Unknown:
    """Как WS2 #378: неизвестное имя не KeyError, а карточка high. Список имён уже."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.known = {"look", "create_node"}

    def metadata_registry(self) -> dict[str, dict[str, object]]:
        return {
            name: {"name": name, "risk": "none", "read_only": True, "destructive": False, "paid": False}
            for name in self.known
        }

    def metadata_of(self, name: str) -> dict[str, object]:
        listed = self.metadata_registry()
        if name in listed:
            return listed[name]
        return {"name": name, "risk": "high", "read_only": False, "destructive": False, "paid": False}

    async def execute(self, name: str, params: dict, task_ctx: dict) -> dict:
        del params, task_ctx
        self.calls.append(name)
        return {"ok": True, "data": {}, "change_id": None, "cost": 0.0}


def test_bridge_with_ws2_metadata_of_refuses_unknown() -> None:
    registry = _Ws2Unknown()
    for name in ("frobnicate", "exec", "edit_file"):
        assert not_in_whitelist(name, registry) is True
    assert not_in_whitelist("look", registry) is False
    assert not_in_whitelist("look", _Nameless()) is True
    assert not_in_whitelist("look", _EmptyMeta()) is True
    assert not_in_whitelist("look", _Mismatch()) is True


class _DangerousUnlisted:
    """Нет metadata_registry и whitelist: карточка metadata_of не открывает имя."""

    def metadata_of(self, name: str) -> dict[str, object]:
        return {
            "name": name,
            "risk": "high",
            "read_only": False,
            "destructive": True,
            "paid": True,
        }


class _FlaggedUnlisted(_DangerousUnlisted):
    allow_unlisted = True


def test_no_registry_and_no_whitelist_refuses_unknown_and_dangerous() -> None:
    registry = _DangerousUnlisted()
    assert not hasattr(registry, "metadata_registry")
    assert not hasattr(registry, "whitelist")
    for name in ("frobnicate", "exec"):
        assert not_in_whitelist(name, registry) is True
    flagged = _FlaggedUnlisted()
    assert not_in_whitelist("frobnicate", flagged) is False
    assert not_in_whitelist("bash", flagged) is True
    assert not_in_whitelist("create_node@new_group", flagged) is True


class _Nameless:
    def metadata_of(self, name: str) -> dict[str, object]:
        del name
        return {"risk": "high", "read_only": False, "destructive": True, "paid": False, "soft_delete": True}


class _EmptyMeta:
    def metadata_of(self, name: str) -> dict[str, object]:
        del name
        return {}


class _Mismatch:
    def metadata_of(self, name: str) -> dict[str, object]:
        del name
        return {"name": "other", "risk": "none", "read_only": True, "destructive": False, "paid": False}


@pytest.mark.asyncio
async def test_bridge_execute_does_not_call_inner_for_unlisted() -> None:
    registry = _Ws2Unknown()
    bridge = ActionsBridge(registry)
    for name in ("frobnicate", "exec", "edit_file"):
        await bridge.execute(name, {}, {})
    assert registry.calls == []


@pytest.mark.asyncio
async def test_resume_of_look_then_frobnicate_executes_nothing(token: str) -> None:
    del token
    actions = _Ws2Unknown()
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([]),
    )
    set_runtime(runtime)
    session = "wl-resume"
    try:
        steps = [
            PlanStep(action="look", risk="none"),
            PlanStep(action="frobnicate", risk="none"),
        ]
        task_id = runtime.tasks.insert(session, "план", TaskStatus.paused.value, project_id=13)
        runtime.tasks.save_plan(task_id, steps, position=0)
        CheckpointStore().save(task_id, session, CheckpointData(plan=steps, position=0))
        await runtime.supervisor.resume(task_id)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
    finally:
        set_runtime(None)
