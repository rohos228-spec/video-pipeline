"""R3 на моках: план, критик, interrupt/steer/append, kill, resume."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from app.orchestrator.agents.critic import PlanCritic
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import VALIDATOR_CONTEXT_DRY_RUN, Chunk, ChunkCorpus, PlanStep, TaskStatus
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedCritic, ScriptedRouter


class MemoryRag:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    async def retrieve(
        self,
        query: str,
        corpora: list[str] | None = None,
        top_k: int = 8,
    ) -> list[Chunk]:
        del query, corpora, top_k
        self.calls += 1
        return [
            Chunk(
                chunk_id="rule-1",
                corpus=ChunkCorpus.rules,
                source_id="user-rules",
                title="rules",
                text=self.text,
            )
        ]


class CountingPlanner:
    def __init__(self, steps: list[PlanStep]) -> None:
        self.steps = list(steps)
        self.calls = 0

    async def make_plan(self, goal: str, context: str = "") -> list[PlanStep]:
        del goal, context
        self.calls += 1
        return list(self.steps)


class SteerPlanner:
    async def make_plan(self, goal: str, context: str = "") -> list[PlanStep]:
        del goal
        if "измени" in context:
            return [
                PlanStep(
                    action="after",
                    params={"_change_id": "chg-b"},
                    expected="after",
                    paid=False,
                    mutating=True,
                )
            ]
        return [
            PlanStep(
                action="before",
                params={"_change_id": "chg-a"},
                expected="before",
                paid=False,
                mutating=True,
            ),
            PlanStep(
                action="stale",
                params={"_change_id": "chg-stale"},
                expected="stale",
                paid=False,
                mutating=True,
            ),
        ]


def _five_steps() -> list[dict[str, Any]]:
    names = ["look", "list", "note", "check", "save"]
    steps: list[dict[str, Any]] = []
    for name in names:
        steps.append(
            {
                "action": name,
                "params": {},
                "expected": name,
                "paid": False,
                "mutating": name == "note",
                "risk": "high" if name == "note" else "none",
                "estimated_cost": 0,
            }
        )
    return steps


@pytest.mark.asyncio
async def test_five_step_plan_asks_one_approval(token: str) -> None:
    del token
    router = ScriptedRouter()
    router.script = [json.dumps({"steps": _five_steps()}), "approve"]
    rag = MemoryRag("НЕ УДАЛЯЙ КАДРЫ")
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router,
        planner=RouterPlanner(router, rag),
        critic=PlanCritic(router, rag),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-five", "сделай обход")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        assert [call["name"] for call in actions.calls] == ["look", "list"]
        approvals = [event for event in rt.bus.replay("r3-five", 0) if event.type == "approval_required"]
        assert len(approvals) == 1
        assert approvals[0].payload["reason"] == "risk"
        assert approvals[0].payload["action"] == "note"
        assert rag.calls >= 1
        assert "НЕ УДАЛЯЙ КАДРЫ" in router.seen_messages[0][0]["content"]
        await rt.supervisor.grant_approval("r3-five")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert [call["name"] for call in actions.calls] == ["look", "list", "note", "check", "save"]
        assert len([event for event in rt.bus.replay("r3-five", 0) if event.type == "approval_required"]) == 1
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_two_revises_escalate(token: str) -> None:
    del token
    planner = CountingPlanner([PlanStep(action="noop", expected="ok")])
    rt = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=planner,
        critic=ScriptedCritic(["revise", "revise", "revise"]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-rev", "цель")
        await rt.supervisor.wait(task_id, timeout=5)
        assert planner.calls == 3
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        approvals = [event for event in rt.bus.replay("r3-rev", 0) if event.type == "approval_required"]
        assert approvals[-1].payload["reason"] == "critic_escalation"
        assert rt.tasks.list_steps(task_id) == []
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_interrupt_during_mock_llm_under_ten_seconds(token: str) -> None:
    del token
    router = ScriptedRouter()
    router.call_delay = 30
    rt = build_runtime(
        actions=MockActions(),
        validators=MockValidators(),
        router=router,
        planner=RouterPlanner(router, MemoryRag("правило")),
        critic=PlanCritic(router, MemoryRag("правило")),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-llm", "план")
        await router.call_started.wait()
        started = time.perf_counter()
        await rt.queue.accept("r3-llm", "отмена")
        pending = list(rt.queue._pending)
        if pending:
            await asyncio.wait_for(asyncio.gather(*pending), timeout=2)
        await rt.supervisor.wait(task_id, timeout=8)
        assert time.perf_counter() - started < 10.0
        assert rt.tasks.get(task_id)["status"] == TaskStatus.cancelled.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_switch_cancels_unpaid_within_two_seconds(token: str) -> None:
    del token
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="slow", params={"_block": True, "_seconds": 30}, paid=False),
                PlanStep(action="later", paid=False),
            ]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-kill", "долгий")
        await actions.started.wait()
        started = time.perf_counter()
        rt.supervisor.request_kill("r3-kill")
        await rt.supervisor.wait(task_id, timeout=2)
        assert time.perf_counter() - started < 2.0
        await asyncio.sleep(0)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.cancelled.value
        assert [call["name"] for call in actions.calls] == ["slow"]
        kill = next(event for event in rt.bus.replay("r3-kill", 0) if event.type == "kill_switch")
        assert kill.payload["stop_key_set"] is True
        assert kill.payload["only_node"] is True
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_switch_pauses_paid_step_at_boundary(token: str) -> None:
    del token
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="paid_mock", params={"_wait": True, "_cost": 1}, paid=True, mutating=True),
                PlanStep(action="later", paid=True, mutating=True),
            ]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-kill-paid", "платный")
        await actions.started.wait()
        started = time.perf_counter()
        rt.supervisor.request_kill("r3-kill-paid")
        for _ in range(50):
            if any(event.type == "kill_switch" for event in rt.bus.replay("r3-kill-paid", 0)):
                break
            await asyncio.sleep(0.01)
        assert time.perf_counter() - started < 2.0
        assert rt.tasks.get(task_id)["status"] == TaskStatus.executing.value
        actions.release.set()
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.paused.value
        assert [call["name"] for call in actions.calls] == ["paid_mock"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_steer_replans_without_redoing_checkpoint(token: str) -> None:
    del token
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=SteerPlanner(),
    )
    set_runtime(rt)

    async def _steer(name: str, params: dict[str, Any], ctx: dict[str, Any]) -> None:
        del params, ctx
        if name == "before":
            rt.supervisor.request_steer("r3-steer", "измени план")

    actions.on_execute = _steer
    try:
        task_id = await rt.supervisor.start_task("r3-steer", "цель")
        await rt.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["before", "after"]
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        latest = rt.checkpoints.load_latest(task_id)
        assert latest is not None
        assert latest.change_ids == ["chg-a", "chg-b"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_append_does_not_change_plan(token: str) -> None:
    del token
    actions = MockActions()
    router = ScriptedRouter()
    router.answer = "полдень"
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=router,
        planner=FixedPlanner(
            [
                PlanStep(action="hold", params={"_wait": True}, paid=False, mutating=False),
                PlanStep(action="tail", paid=False, mutating=False),
            ]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-append", "долгая")
        await actions.started.wait()
        saved = rt.tasks.get(task_id)["plan_json"]
        await rt.queue.accept("r3-append", "который час?")
        pending = list(rt.queue._pending)
        if pending:
            await asyncio.wait_for(asyncio.gather(*pending), timeout=2)
        assert rt.tasks.get(task_id)["plan_json"] == saved
        assert rt.tasks.get(task_id)["status"] == TaskStatus.executing.value
        actions.release.set()
        await rt.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["hold", "tail"]
        assert rt.tasks.get(task_id)["plan_json"] == saved
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_resume_after_process_restart(token: str) -> None:
    del token
    actions = MockActions()
    steps = [
        PlanStep(action="keep", params={"_change_id": "chg-keep"}, mutating=True),
        PlanStep(action="next", params={"_change_id": "chg-next"}, mutating=True),
    ]
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(steps),
    )

    async def _pause(name: str, params: dict[str, Any], ctx: dict[str, Any]) -> None:
        del params, ctx
        if name == "keep":
            rt.supervisor.request_pause("r3-resume")

    actions.on_execute = _pause
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-resume", "доделать")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.paused.value
        actions.on_execute = None
        restarted = build_runtime(
            actions=actions,
            validators=MockValidators(),
            router=ScriptedRouter(),
            planner=FixedPlanner(steps),
        )
        assert restarted.supervisor is not rt.supervisor
        assert task_id not in restarted.supervisor._change_ids
        set_runtime(restarted)
        await restarted.supervisor.resume(task_id)
        assert restarted.supervisor._change_ids[task_id] == ["chg-keep"]
        await restarted.supervisor.wait(task_id, timeout=5)
        assert restarted.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert [call["name"] for call in actions.calls] == ["keep", "next"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_frame_deletion_is_blocked(token: str) -> None:
    del token
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [PlanStep(action="montage.delete_frame", params={"frame_id": 4}, mutating=True, risk="high")]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-del", "удали кадр")
        await rt.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert rt.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        reason = next(
            event.payload["reason"]
            for event in rt.bus.replay("r3-del", 0)
            if event.type == "approval_required"
        )
        assert reason == "deletion_blocked"
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_verify_code_state_required_before_run_node(token: str) -> None:
    del token
    blocked = MockActions()
    bad = build_runtime(
        actions=blocked,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="run_node", params={"node_key": "n_qc"}, paid=True, mutating=True)]),
    )
    set_runtime(bad)
    try:
        task_id = await bad.supervisor.start_task("r3-verify-bad", "запусти")
        await bad.supervisor.wait(task_id, timeout=5)
        assert blocked.calls == []
        assert bad.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        reason = next(
            event.payload["reason"]
            for event in bad.bus.replay("r3-verify-bad", 0)
            if event.type == "approval_required"
        )
        assert reason == "plan_sanity"
    finally:
        set_runtime(None)

    allowed = MockActions()
    good = build_runtime(
        actions=allowed,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="verify_code_state", expected="git+node"),
                PlanStep(action="run_node", params={"node_key": "n_qc"}, paid=True, mutating=True),
            ]
        ),
    )
    set_runtime(good)
    try:
        task_id = await good.supervisor.start_task("r3-verify-ok", "запусти")
        await good.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in allowed.calls] == ["verify_code_state", "run_node"]
        assert good.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_read_and_get_node_satisfy_rule_nine(token: str) -> None:
    del token
    actions = MockActions()
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="read_pipeline_code", params={"path": "app/orchestrator/agents/supervisor.py"}),
                PlanStep(action="get_node", params={"node_key": "n_qc"}),
                PlanStep(action="run_node", params={"node_key": "n_qc"}, paid=True, mutating=True),
            ]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-verify-pair", "запусти")
        await rt.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["read_pipeline_code", "get_node", "run_node"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_paid_retry_guard_skips_second_payment(token: str) -> None:
    del token
    actions = MockActions()
    paid = PlanStep(
        action="paid_mock",
        params={"node_key": "n_a", "_cost": 1},
        paid=True,
        mutating=True,
    )
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([paid, paid.model_copy(deep=True)]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-paid", "ещё раз")
        await rt.supervisor.wait(task_id, timeout=5)
        assert [call["name"] for call in actions.calls] == ["paid_mock"]
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        blocked = [event for event in rt.bus.replay("r3-paid", 0) if event.type == "paid_retry_blocked"]
        assert blocked and blocked[0].payload["reason"] == "paid_retry"
        assert float(rt.tasks.get(task_id)["spend_actions"]) == 1.0
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_readonly_group_runs_in_parallel_mutations_do_not(token: str) -> None:
    del token
    actions = MockActions()
    current = 0
    peak = 0

    async def _track(name: str, params: dict[str, Any], ctx: dict[str, Any]) -> None:
        del name, params, ctx
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.05)
        current -= 1

    actions.on_execute = _track
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="a", parallel_group="ro"),
                PlanStep(action="b", parallel_group="ro"),
            ]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-par", "чтение")
        await rt.supervisor.wait(task_id, timeout=5)
        assert peak == 2
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
    finally:
        set_runtime(None)

    actions = MockActions()
    current = 0
    peak = 0

    async def _track_seq(name: str, params: dict[str, Any], ctx: dict[str, Any]) -> None:
        del name, params, ctx
        nonlocal current, peak
        current += 1
        peak = max(peak, current)
        await asyncio.sleep(0.05)
        current -= 1

    actions.on_execute = _track_seq
    rt = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(action="m1", parallel_group="same", mutating=True),
                PlanStep(action="m2", parallel_group="same", mutating=True),
            ]
        ),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-seq", "мутации")
        await rt.supervisor.wait(task_id, timeout=5)
        assert peak == 1
        assert [call["name"] for call in actions.calls] == ["m1", "m2"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_critic_blocks_spend_forecast_and_deletion(token: str) -> None:
    del token
    critic = PlanCritic(router=None, rag=None)
    assert await critic.review([PlanStep(action="noop", estimated_cost=41)]) == "revise"
    assert await critic.review([PlanStep(action="montage.delete_frame")]) == "reject"
    assert await critic.review([PlanStep(action="run_node")]) == "revise"
    assert await critic.review([PlanStep(action="noop", estimated_cost=1)]) == "approve"


@pytest.mark.asyncio
async def test_dry_run_before_critic_sets_read_only_flag(token: str) -> None:
    del token
    validators = MockValidators()
    rt = build_runtime(
        actions=MockActions(),
        validators=validators,
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="noop", mutating=True, expected="ok")]),
    )
    set_runtime(rt)
    try:
        task_id = await rt.supervisor.start_task("r3-dry", "проверка")
        await rt.supervisor.wait(task_id, timeout=5)
        assert rt.tasks.get(task_id)["status"] == TaskStatus.done.value
        dry = [call for call in validators.calls if call["context"].get(VALIDATOR_CONTEXT_DRY_RUN) is True]
        assert [call["name"] for call in dry] == ["plan_sanity", "no_deletions", "spend_guard"]
        assert all(call["context"][VALIDATOR_CONTEXT_DRY_RUN] is True for call in dry)
        live = [call for call in validators.calls if call not in dry]
        assert live
        assert all(call["context"].get(VALIDATOR_CONTEXT_DRY_RUN) is not True for call in live)
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_kill_phrase_is_deterministic(token: str) -> None:
    del token
    rt = build_runtime(actions=MockActions(), validators=MockValidators(), router=ScriptedRouter())
    set_runtime(rt)
    try:
        await rt.queue.accept("r3-phrase", "аварийный стоп")
        pending = list(rt.queue._pending)
        if pending:
            await asyncio.wait_for(asyncio.gather(*pending), timeout=2)
        assert rt.router.classify_calls == 0
        classification = next(event for event in rt.bus.replay("r3-phrase", 0) if event.type == "classification")
        assert classification.payload["mode"] == "interrupt"
        assert classification.payload["kill_switch"] is True
        kill = next(event for event in rt.bus.replay("r3-phrase", 0) if event.type == "kill_switch")
        assert kill.payload["stop_key_set"] is True
    finally:
        set_runtime(None)
