"""Правила пользователя остаются в system, если RAG пустой или упал."""

from __future__ import annotations

import os
import sys
import types

import pytest

from app.orchestrator.agents.critic import RouterCritic
from app.orchestrator.agents.planner import RouterPlanner
from app.orchestrator.agents.runtime import get_runtime, set_runtime
from app.orchestrator.contracts import PlanStep
from tests.agents.mocks import ScriptedRouter

_RULE = "ПРАВИЛО-ПОЛЬЗОВАТЕЛЯ"


class _RuleRag:
    def __init__(self, *, fail: bool) -> None:
        self.fail = fail
        self.calls = 0

    def rules_system_block(self) -> str:
        return _RULE

    async def retrieve(self, query: str, corpora: list[str] | None = None, top_k: int = 8):
        del query, corpora, top_k
        self.calls += 1
        if self.fail:
            raise RuntimeError("rag down")
        return []


async def _systems(rag: _RuleRag) -> tuple[str, str]:
    router = ScriptedRouter()
    router.script = ["{}", "approve"]
    planner = RouterPlanner(router, rag)
    critic = RouterCritic(router, rag)
    await planner.make_plan("цель", run_id="run-rag", task_id=1)
    await critic.review([PlanStep(action="noop")], run_id="run-rag", task_id=1)
    planner_system = router.seen_messages[0][0]["content"]
    critic_system = router.seen_messages[1][0]["content"]
    return planner_system, critic_system


@pytest.mark.asyncio
async def test_user_rules_stay_in_system_when_rag_empty_or_fails() -> None:
    for fail in (False, True):
        rag = _RuleRag(fail=fail)
        planner_system, critic_system = await _systems(rag)
        assert rag.calls == 2
        assert _RULE in planner_system
        assert _RULE in critic_system
        assert "Не планируй удаление кадров и сцен." in planner_system
        assert "Правила:" in critic_system


@pytest.mark.asyncio
async def test_production_rag_uses_module_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCH_RUNTIME", raising=False)
    monkeypatch.delenv("ORCH_RAG_PROFILE", raising=False)
    seen: list[tuple] = []
    blocks: list[str] = []
    resets: list[str] = []
    module = types.ModuleType("app.orchestrator.rag")

    def retrieve(query: str, corpora: list[str] | tuple[str, ...] | None = None, top_k: int = 8, **kwargs: object):
        seen.append((query, corpora, top_k, kwargs))
        return []

    def rules_system_block() -> str:
        blocks.append(_RULE)
        return _RULE

    def reset_engine() -> None:
        resets.append("reset")

    module.retrieve = retrieve
    module.rules_system_block = rules_system_block
    module.reset_engine = reset_engine
    monkeypatch.setitem(sys.modules, "app.orchestrator.rag", module)
    set_runtime(None)
    try:
        assert os.environ.get("ORCH_RAG_PROFILE") in {None, ""}
        runtime = get_runtime()
        assert "rag" not in runtime.runtime_degraded
        await runtime.supervisor.planner.make_plan("цель", run_id="run-rag", task_id=3)
        await runtime.supervisor.critic.review([PlanStep(action="noop")], run_id="run-rag", task_id=3)
    finally:
        set_runtime(None)
    assert len(seen) == 2
    for _query, corpora, top_k, kwargs in seen:
        assert list(corpora or []) == ["rules"]
        assert top_k == 8
        assert kwargs == {}
    assert blocks == [_RULE, _RULE]
    monkeypatch.setenv("ORCH_RAG_PROFILE", "contour-b")
    module.reset_engine()
    assert resets == ["reset"]
    monkeypatch.delenv("ORCH_RAG_PROFILE", raising=False)
