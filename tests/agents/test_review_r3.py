"""Ревью R3 и стыки WS4/WS5: бюджет, run_id, mode, удаления."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from loguru import logger

from app.orchestrator.agents.critic import _parse_verdict
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.stubs import FallbackRouter
from app.orchestrator.agents.supervisor import InvalidTransition
from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import (
    BLOCK_OUTCOMES,
    ActionMetadata,
    ActionResult,
    ApprovalEvent,
    LLMResponse,
    MessageMode,
    PlanStep,
    TaskStatus,
    ValidatorResult,
    block_status,
    is_deletion_action,
    orch_run_id,
    paid_step_identity,
)
from app.orchestrator.db.migrations import connect
from app.web.routers.orchestrator_chat import router as chat_router
from tests.agents.conftest import TEST_TOKEN
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter


def _budget_response(text: str = "бюджет кончился") -> LLMResponse:
    return LLMResponse(
        text=text,
        call_id="budget-call",
        model="scripted",
        provider="52pay",
        tier="strong",
        finish_reason="stop",
        task_status=TaskStatus.waiting_approval,
        approval=ApprovalEvent(reason="budget_exhausted", payload={"spent_usd": 10}),
    )


class _BudgetPlanner:
    def __init__(self) -> None:
        self.calls = 0
        self.last_response: Any = None

    async def make_plan(self, goal: str, context: str = "", **_call: Any) -> list[PlanStep]:
        del goal, context
        self.calls += 1
        if self.calls == 1:
            self.last_response = _budget_response()
            return [
                PlanStep(action="hold", params={"_wait": True}, paid=False),
                PlanStep(action="tail", paid=False),
            ]
        self.last_response = None
        return [PlanStep(action="after", paid=False)]


class _PayThenBudget:
    def __init__(self, actions: MockActions) -> None:
        self.actions = actions
        self.last_response: Any = None

    async def run(self, step: PlanStep, ctx: dict[str, Any]) -> Any:
        result = await self.actions.execute(step.action, dict(step.params), dict(ctx))
        self.last_response = _budget_response("оплачено, дальше нельзя")
        return result


class _BoomActions(MockActions):
    async def execute(self, name: str, params: dict[str, Any], task_ctx: dict[str, Any]) -> Any:
        self.calls.append({"name": name, "params": dict(params), "ctx": dict(task_ctx)})
        if name == "bad":
            raise RuntimeError("boom")
        return ActionResult(ok=True, data={"name": name}, change_id=f"chg-{name}", cost=0.0)


class _MetaActions(MockActions):
    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, ActionMetadata] = {}

    def metadata_of(self, action_name: str) -> ActionMetadata | None:
        return self.meta.get(action_name)


def _http_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(chat_router, prefix="/api")
    return app


@pytest.mark.asyncio
async def test_internal_call_without_run_id_raises() -> None:
    router = ScriptedRouter()
    with pytest.raises(ValueError, match="run_id"):
        await router.call("strong", [{"role": "user", "content": "x"}])
    with pytest.raises(ValueError, match="run_id"):
        await router.call(
            "strong",
            [{"role": "user", "content": "x"}],
            budget_scope="internal",
            run_id="   ",
        )
    with pytest.raises(ValueError, match="run_id"):
        await FallbackRouter().call("strong", [{"role": "user", "content": "x"}])
    done = await router.call(
        "strong",
        [{"role": "user", "content": "x"}],
        run_id="orch-session-s",
        budget_scope="internal",
    )
    assert done.call_id
    with pytest.raises(ValueError, match="run_id"):
        await router.call(
            "strong",
            [{"role": "user", "content": "x"}],
            budget_scope="pipeline",
            run_id=None,
        )
    with pytest.raises(ValueError, match="run_id"):
        await FallbackRouter().call(
            "strong",
            [{"role": "user", "content": "x"}],
            budget_scope="pipeline",
            run_id="  ",
        )
    piped = await router.call(
        "strong",
        [{"role": "user", "content": "x"}],
        budget_scope="pipeline",
        run_id="orch-session-s",
    )
    assert piped.call_id


@pytest.mark.asyncio
async def test_classify_reserves_session_run_before_the_model(runtime) -> None:
    await runtime.queue.accept("pre-run", "сделай что-нибудь")
    await runtime.queue.drain()
    assert runtime.router.last_classify["run_id"] == orch_run_id(session_id="pre-run")
    assert runtime.router.last_call["run_id"] == orch_run_id(session_id="pre-run")
    assert runtime.router.classify_calls == 1


@pytest.mark.asyncio
async def test_paid_step_is_kept_when_budget_trips(token: str) -> None:
    del token
    actions = MockActions()
    step = PlanStep(
        action="charge",
        params={"_change_id": "chg-paid", "_cost": 4.0},
        paid=True,
        mutating=True,
    )
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([step]),
        executor=_PayThenBudget(actions),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-pay", "оплатить", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        row = rt.tasks.list_steps(task_id)[0]
        assert row["status"] == "done"
        assert row["change_id"] == "chg-paid"
        assert row["cost"] == pytest.approx(4.0)
        checkpoint = rt.checkpoints.load_latest(task_id)
        assert checkpoint is not None
        assert checkpoint.position == 1

        restarted = build_runtime(
            actions=actions,
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=FixedPlanner([step]),
            executor=_PayThenBudget(actions),
        )
        set_runtime(restarted)
        before = len(actions.calls)
        await restarted.supervisor.resume(task_id)
        await restarted.supervisor.wait(task_id, timeout=5)
        assert len(actions.calls) == before
        assert restarted.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_grant_clears_budget_stop_and_steer_replans(token: str) -> None:
    del token
    actions = MockActions()
    planner = _BudgetPlanner()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=planner,
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-grant", "план", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert task_id in rt.supervisor._budget_stop
        await rt.supervisor.grant_approval("r3-grant")
        assert task_id not in rt.supervisor._budget_stop
        await actions.started.wait()
        rt.supervisor.request_steer("r3-grant", "учти что дальше")
        actions.release.set()
        await rt.supervisor.wait(task_id, timeout=5)
        assert planner.calls >= 2
        assert "after" in [call["name"] for call in actions.calls]
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_queued_task_waits_out_a_paused_one(token: str) -> None:
    del token
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="keep", mutating=True),
                PlanStep(action="next", mutating=True),
            ]
        ),
    )

    async def _pause(name: str, params: dict[str, Any], ctx: dict[str, Any]) -> None:
        del params, ctx
        if name == "keep":
            rt.supervisor.request_pause("r3-q")

    actions.on_execute = _pause
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-q", "первая", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.paused.value
        queued_id = await rt.supervisor.submit_task("r3-q", "вторая", project_id=13)
        assert rt.tasks.get(queued_id)["status"] == TaskStatus.queued.value
        assert rt.supervisor.active_task("r3-q") == task_id
        assert [call["name"] for call in actions.calls] == ["keep"]
        actions.on_execute = None
        await rt.supervisor.resume(task_id)
        await rt.supervisor.wait(task_id, timeout=5)
        await rt.supervisor.wait(queued_id, timeout=5)
        assert rt.tasks.get(queued_id)["status"] == TaskStatus.done.value
        assert [call["name"] for call in actions.calls] == ["keep", "next", "keep", "next"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_parallel_failure_keeps_sibling_done(token: str) -> None:
    del token
    actions = _BoomActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="good", parallel_group="g"),
                PlanStep(action="bad", parallel_group="g"),
            ]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-gather", "группа")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.failed.value
        rows = {row["action_name"]: row["status"] for row in rt.tasks.list_steps(task_id)}
        assert rows["good"] == "done"
        assert rows["bad"] == "failed"
        assert next(row["change_id"] for row in rt.tasks.list_steps(task_id) if row["action_name"] == "good") == "chg-good"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["montage.remove_frame", "shots.purge", "scene.drop"])
async def test_remove_purge_drop_are_blocked_by_name(token: str, action: str) -> None:
    del token
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action=action, mutating=True)]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task(f"r3-{action}", "удалить")
        await rt.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert rt.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert TaskStatus.waiting_approval.value not in [status for _, status in rt.supervisor.history]
        failed = [event for event in rt.bus.replay(f"r3-{action}", 0) if event.type == "task_failed"]
        assert failed[-1].payload["error"] == "deletion_blocked"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_destructive_metadata_fails_without_approval(token: str) -> None:
    del token
    actions = _MetaActions()
    actions.meta["scene.drop"] = ActionMetadata(risk="high", read_only=False, destructive=True)
    actions.meta["look"] = ActionMetadata(risk="none", read_only=True, destructive=False)
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="scene.drop", mutating=True)]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-hard", "запрет")
        await rt.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert rt.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert not [event for event in rt.bus.replay("r3-hard", 0) if event.type == "approval_required"]
    finally:
        set_runtime(None)

    unknown = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="unlisted", mutating=True)]),
    )
    set_runtime(unknown)
    try:
        task_id = await unknown.supervisor.start_task("r3-unknown", "нет в реестре")
        await unknown.supervisor.wait(task_id, timeout=5)
        assert unknown.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert actions.calls == []
    finally:
        set_runtime(None)

    allowed = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="look")]),
    )
    set_runtime(allowed)
    try:
        task_id = await allowed.supervisor.start_task("r3-look", "читать")
        await allowed.supervisor.wait(task_id, timeout=5)
        assert allowed.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert [call["name"] for call in actions.calls] == ["look"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_skip_uses_stored_change_id_not_param(token: str) -> None:
    del token
    actions = MockActions()
    step = PlanStep(action="keep", params={"frame": 1}, mutating=True)
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([step]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-chg", "сдать", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        stored = rt.tasks.list_steps(task_id)[0]["change_id"]
        assert stored
        assert "_change_id" not in step.params
        conn = connect()
        try:
            conn.execute("UPDATE orch_tasks SET status = 'paused' WHERE id = ?", (task_id,))
            conn.commit()
        finally:
            conn.close()
        data = rt.checkpoints.load_latest(task_id)
        assert data is not None
        data.position = 0
        rt.checkpoints.save(task_id, "r3-chg", data)
        restarted = build_runtime(
            actions=actions,
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=FixedPlanner([step]),
        )
        set_runtime(restarted)
        before = len(actions.calls)
        await restarted.supervisor.resume(task_id)
        await restarted.supervisor.wait(task_id, timeout=5)
        assert len(actions.calls) == before
        skipped = [
            event
            for event in restarted.bus.replay("r3-chg", 0)
            if event.type == "step_skipped" and event.payload.get("reason") == "change_already_applied"
        ]
        assert skipped
    finally:
        set_runtime(None)


def test_unrecognized_critic_verdict_logs_raw_text() -> None:
    lines: list[str] = []
    handle = logger.add(lambda message: lines.append(str(message)))
    try:
        assert _parse_verdict("пожалуй approve позже") == "revise"
    finally:
        logger.remove(handle)
    assert any("пожалуй approve позже" in line for line in lines)


@pytest.mark.asyncio
async def test_message_mode_task_steer_interrupt_append_and_garbage(token: str) -> None:
    del token
    actions = MockActions()
    router = ScriptedRouter()
    router.answer = "ответ"
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router,
        planner=FixedPlanner([PlanStep(action="hold", params={"_wait": True}, paid=False)]),
    )
    set_runtime(rt)
    transport = ASGITransport(app=_http_app())
    headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
    try:
        async def _flush() -> None:
            await rt.queue.drain()

        task_id = await rt.supervisor.start_task("mode-s", "держи")
        await actions.started.wait()
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            garbage = await client.post(
                "/api/orchestrator/chat/mode-s/message",
                json={"text": "мусор", "mode": "nope"},
                headers=headers,
            )
            assert garbage.status_code == 422
            steer = await client.post(
                "/api/orchestrator/chat/mode-s/message",
                json={"text": "новый курс", "mode": "steer"},
                headers=headers,
            )
            await _flush()
            interrupt = await client.post(
                "/api/orchestrator/chat/mode-s/message",
                json={"text": "хватит", "mode": "interrupt"},
                headers=headers,
            )
            await _flush()
            appended = await client.post(
                "/api/orchestrator/chat/mode-s/message",
                json={"text": "просто реплика", "mode": "append"},
                headers=headers,
            )
            await _flush()
            created = await client.post(
                "/api/orchestrator/chat/mode-fresh/message",
                json={"text": "отдельная задача", "mode": "task"},
                headers=headers,
            )
            await _flush()
        assert steer.status_code == 200
        assert interrupt.status_code == 200
        assert appended.status_code == 200
        assert created.status_code == 200
        assert rt.tasks.get(task_id)["steer_text"] == "новый курс"
        assert "mode-s" in rt.supervisor._cancel
        assert router.classify_calls == 0
        tokens = "".join(
            event.payload.get("text", "")
            for event in rt.bus.replay("mode-s", 0)
            if event.type == "token"
        )
        assert "ответ" in tokens
        goals = [row["goal"] for row in (rt.tasks.get(item) for item in rt.supervisor._runs) if row]
        assert "отдельная задача" in goals
        actions.release.set()
        for run in list(rt.supervisor._runs.values()):
            await asyncio.wait_for(run, timeout=5)
    finally:
        set_runtime(None)


def test_block_table_replaces_revise_limit() -> None:
    assert "revise_limit" not in BLOCK_OUTCOMES
    assert block_status("budget_exhausted") is TaskStatus.waiting_approval
    assert block_status("critic_escalation") is TaskStatus.waiting_approval
    assert block_status("missing_project_id") is TaskStatus.waiting_approval
    assert block_status("deletion_blocked") is TaskStatus.failed
    assert block_status("missing_run_id") is TaskStatus.failed
    assert block_status("empty_plan") is TaskStatus.failed
    assert block_status("validator_blocked") is TaskStatus.failed


def test_deletion_markers_are_name_segments() -> None:
    assert is_deletion_action("montage.remove_frame")
    assert is_deletion_action("shots.purge")
    assert is_deletion_action("scene.drop")
    assert not is_deletion_action("dropdown")
    assert is_deletion_action("remove_watermark")


class _RecordingValidators:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def run(self, name: str, project_id: int, context: dict[str, Any]) -> ValidatorResult:
        self.calls.append({"name": name, "project_id": project_id, "context": dict(context)})
        return ValidatorResult(name=name, passed=True, details="ok", blocking=False)


class _EmptyBudgetPlanner:
    def __init__(self) -> None:
        self.last_response: Any = None

    async def make_plan(self, goal: str, context: str = "", **_call: Any) -> list[PlanStep]:
        del goal, context
        self.last_response = _budget_response("пусто")
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("step", "risks"),
    [
        (PlanStep(action="edit", mutating=True), None),
        (PlanStep(action="charge", paid=True), None),
        (PlanStep(action="touch"), {"touch": "high"}),
    ],
)
async def test_missing_project_id_waits_for_paid_or_mutating(
    token: str,
    step: PlanStep,
    risks: dict[str, str] | None,
) -> None:
    del token
    validators = _RecordingValidators()
    actions = MockActions(risks)
    rt = build_runtime(
        actions=actions,
        validators=validators,
        router=ScriptedRouter(),
        planner=FixedPlanner([step]),
    )
    set_runtime(rt)
    session = f"r3-nopid-{step.action}-{step.paid}-{step.mutating}"
    try:
        task_id = await rt.supervisor.start_task(session, "без проекта")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == BLOCK_OUTCOMES["missing_project_id"].value
        assert actions.calls == []
        assert validators.calls == []
        approvals = [event for event in rt.bus.replay(session, 0) if event.type == "approval_required"]
        assert approvals[-1].payload["reason"] == "missing_project_id"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_readonly_plan_without_project_id_runs(token: str) -> None:
    del token
    validators = _RecordingValidators()
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=validators,
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="look")]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-ro", "только чтение")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert [call["name"] for call in actions.calls] == ["look"]
        assert validators.calls == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_message_project_id_reaches_validator_context(token: str) -> None:
    del token
    validators = _RecordingValidators()
    rt = build_runtime(
        actions=MockActions(),
        validators=validators,
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="noop", mutating=True)]),
    )
    set_runtime(rt)
    transport = ASGITransport(app=_http_app())
    headers = {"Authorization": f"Bearer {TEST_TOKEN}"}
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/orchestrator/chat/pid-chat/message",
                json={"text": "с проектом", "mode": "task", "project_id": 13},
                headers=headers,
            )
        assert created.status_code == 200
        await rt.queue.drain()
        task_id = next(iter(rt.supervisor._runs))
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["project_id"] == 13
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert validators.calls
        assert {call["project_id"] for call in validators.calls} == {13}
        assert all(call["context"]["project_id"] == 13 for call in validators.calls)
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_grant_after_deletion_blocked_does_not_execute(token: str) -> None:
    del token
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="montage.delete_frame", mutating=True)]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-grant-del", "удали")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == BLOCK_OUTCOMES["deletion_blocked"].value
        with pytest.raises(InvalidTransition):
            await rt.supervisor.grant_approval("r3-grant-del")
        assert actions.calls == []
        assert rt.tasks.get(task_id)["status"] == TaskStatus.failed.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_grant_rescreens_and_refuses_a_deletion_plan(token: str) -> None:
    del token
    actions = MockActions(risks={"charge": "high"})
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="charge", risk="low", mutating=True)]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-rescreen", "риск", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert actions.calls == []
        current = rt.checkpoints.load_latest(task_id)
        assert current is not None
        rt.checkpoints.save(
            task_id,
            "r3-rescreen",
            current.model_copy(
                update={
                    "plan": [PlanStep(action="montage.delete_frame", mutating=True)],
                    "position": 0,
                }
            ),
        )
        await rt.supervisor.grant_approval("r3-rescreen")
        assert actions.calls == []
        assert rt.tasks.get(task_id)["status"] == BLOCK_OUTCOMES["deletion_blocked"].value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_empty_checkpoint_plan_fails_on_grant_and_resume(token: str) -> None:
    del token
    grant_rt = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=_EmptyBudgetPlanner(),
    )
    set_runtime(grant_rt)
    try:
        grant_id = await grant_rt.supervisor.start_task("r3-empty-grant", "пусто")
        await grant_rt.supervisor.wait(grant_id, timeout=5)
        assert grant_rt.tasks.get(grant_id)["status"] == TaskStatus.waiting_approval.value
        saved = grant_rt.checkpoints.load_latest(grant_id)
        assert saved is not None
        assert saved.plan == []
        await grant_rt.supervisor.grant_approval("r3-empty-grant")
        assert grant_rt.tasks.get(grant_id)["status"] == BLOCK_OUTCOMES["empty_plan"].value
        assert grant_rt.tasks.get(grant_id)["error"] == "empty_plan"
    finally:
        set_runtime(None)

    resume_rt = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=_EmptyBudgetPlanner(),
    )
    set_runtime(resume_rt)
    try:
        resume_id = await resume_rt.supervisor.start_task("r3-empty-resume", "пусто")
        await resume_rt.supervisor.wait(resume_id, timeout=5)
        restarted = build_runtime(
            actions=MockActions(),
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=_EmptyBudgetPlanner(),
        )
        set_runtime(restarted)
        await restarted.supervisor.resume(resume_id)
        assert restarted.tasks.get(resume_id)["status"] == BLOCK_OUTCOMES["empty_plan"].value
        assert restarted.tasks.get(resume_id)["error"] == "empty_plan"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_append_budget_without_task_publishes(token: str) -> None:
    del token
    router = ScriptedRouter()
    router.responses = [_budget_response("сессия без задачи")]
    rt = build_runtime(actions=MockActions(), validators=MockValidators(), router=router)
    set_runtime(rt)
    try:
        await rt.queue.accept("r3-append-budget", "который час?")
        await rt.queue.drain()
        approvals = [event for event in rt.bus.replay("r3-append-budget", 0) if event.type == "approval_required"]
        assert approvals[-1].payload["reason"] == "budget_exhausted"
        assert approvals[-1].payload["task_id"] is None
        assert rt.supervisor.active_task("r3-append-budget") is None
    finally:
        set_runtime(None)


class _GrantSteerPlanner:
    async def make_plan(self, goal: str, context: str = "", **_call: Any) -> list[PlanStep]:
        del goal
        if "другое" in context:
            return [PlanStep(action="other", risk="high", mutating=True)]
        return [
            PlanStep(action="first", risk="high", mutating=True),
            PlanStep(action="tail", paid=False),
        ]


class _FailOk(MockActions):
    async def execute(self, name: str, params: dict[str, Any], task_ctx: dict[str, Any]) -> ActionResult:
        self.calls.append({"name": name, "params": dict(params), "ctx": dict(task_ctx)})
        self.started.set()
        return ActionResult(
            ok=name != "bad",
            data={"name": name},
            change_id=f"chg-{name}",
            cost=3.0 if name == "bad" else 0.0,
        )


@pytest.mark.asyncio
async def test_grant_then_steer_requires_approval_for_a_different_action(token: str) -> None:
    del token
    actions = MockActions(risks={"first": "high", "other": "high", "tail": "none"})
    session = "r3-grant-steer"
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=_GrantSteerPlanner(),
    )
    set_runtime(rt)

    async def _steer(name: str, params: dict[str, Any], ctx: dict[str, Any]) -> None:
        del params, ctx
        if name == "first":
            rt.supervisor.request_steer(session, "сделай другое")

    actions.on_execute = _steer
    first = PlanStep(action="first", risk="high", mutating=True)
    other = PlanStep(action="other", risk="high", mutating=True)
    assert rt.supervisor._risk_grant_key(1, 0, first) != rt.supervisor._risk_grant_key(1, 0, other)
    try:
        task_id = await rt.supervisor.start_task(session, "цель", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        await rt.supervisor.grant_approval(session)
        await rt.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["first"]
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        approvals = [event for event in rt.bus.replay(session, 0) if event.type == "approval_required"]
        assert approvals[-1].payload["reason"] == "risk"
        assert approvals[-1].payload["action"] == "other"
        assert all(key[0] != task_id for key in rt.supervisor._risk_granted)
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_budget_stop_blocks_the_next_paid_step(token: str) -> None:
    del token
    actions = MockActions()
    session = "r3-mid-budget"
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="free", paid=False),
                PlanStep(action="charge", paid=True, mutating=True),
            ]
        ),
    )
    set_runtime(rt)

    async def _halt(name: str, params: dict[str, Any], ctx: dict[str, Any]) -> None:
        del params, ctx
        if name == "free":
            task_id = rt.supervisor._session_task[session]
            await rt.supervisor.absorb_budget(task_id, session, _budget_response(), position=1)

    actions.on_execute = _halt
    try:
        task_id = await rt.supervisor.start_task(session, "бюджет", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["free"]
        assert rt.tasks.get(task_id)["status"] == BLOCK_OUTCOMES["budget_exhausted"].value
        assert "charge" not in [row["action_name"] for row in rt.tasks.list_steps(task_id)]
        approvals = [event for event in rt.bus.replay(session, 0) if event.type == "approval_required"]
        assert approvals[-1].payload["reason"] == "budget_exhausted"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_failed_action_is_not_done_or_paid(token: str) -> None:
    del token
    actions = _FailOk()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="bad", paid=True, mutating=True),
                PlanStep(action="later", paid=True, mutating=True),
            ]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-ok-false", "сбой", project_id=13)
        await rt.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["bad"]
        assert rt.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert rt.tasks.get(task_id)["error"] == "action_rejected"
        rows = rt.tasks.list_steps(task_id)
        assert [row["action_name"] for row in rows] == ["bad"]
        assert rows[0]["status"] == "failed"
        assert not rows[0]["change_id"]
        assert "chg-bad" not in rt.supervisor._change_ids.get(task_id, [])
        assert paid_step_identity("bad", {}) not in rt.supervisor._paid_done.get(task_id, set())
        assert float(rt.tasks.get(task_id)["spend_actions"] or 0) == 3.0
        finished = [event for event in rt.bus.replay("r3-ok-false", 0) if event.type == "step_finished"]
        assert finished[-1].payload["ok"] is False
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_deletion_with_mutating_false_is_blocked(token: str) -> None:
    del token
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="montage.delete_frame", mutating=False)]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-del-flag", "удалить")
        await rt.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert rt.tasks.get(task_id)["status"] == BLOCK_OUTCOMES["deletion_blocked"].value
        assert rt.tasks.get(task_id)["error"] == "deletion_blocked"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_unknown_action_without_metadata_is_destructive(token: str) -> None:
    del token
    actions = MockActions(risks={"look": "none"})
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="mystery", mutating=False)]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-mystery", "неизвестно")
        await rt.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert rt.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert rt.tasks.get(task_id)["error"] == "destructive_blocked"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_plan_mutating_false_does_not_loosen_registry(token: str) -> None:
    del token
    actions = _MetaActions()
    actions.meta["edit"] = ActionMetadata(risk="low", read_only=False, destructive=False)
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="edit", mutating=False)]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-tighten", "правка")
        await rt.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert rt.tasks.get(task_id)["error"] == "destructive_blocked"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_phrase_overrides_explicit_mode(token: str) -> None:
    del token
    rt = build_runtime(actions=MockActions(), validators=MockValidators(), router=ScriptedRouter())
    set_runtime(rt)
    try:
        await rt.queue.accept("r3-kill-mode", "аварийный стоп", mode=MessageMode.append)
        await rt.queue.drain()
        assert rt.router.classify_calls == 0
        classification = next(event for event in rt.bus.replay("r3-kill-mode", 0) if event.type == "classification")
        assert classification.payload["kill_switch"] is True
        assert classification.payload["mode"] == MessageMode.interrupt.value
        assert "r3-kill-mode" in rt.supervisor._kill
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_apply_error_does_not_publish_a_second_classification(token: str) -> None:
    del token
    rt = build_runtime(actions=MockActions(), validators=MockValidators(), router=ScriptedRouter())
    set_runtime(rt)

    async def _boom(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError("apply")

    rt.queue._apply = _boom
    try:
        await rt.queue.accept("r3-cls-once", "что?")
        await rt.queue.drain()
        classifications = [event for event in rt.bus.replay("r3-cls-once", 0) if event.type == "classification"]
        assert len(classifications) == 1
        assert "error" not in classifications[0].payload
    finally:
        set_runtime(None)
