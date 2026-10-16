from __future__ import annotations

import pytest

from app.services.action_layer.context import ActionContext, WriteNotAllowed
from app.services.action_layer.plan import Plan, PlanFormatError, build_preview, plan_hash
from app.services.action_layer.spec import Precondition
from tests.action_layer_support import fresh_registry, make_spec


def _plan(*steps, text="") -> Plan:
    return Plan.from_json(list(steps), max_steps=20, request_text=text)


def _ctx() -> ActionContext:
    return ActionContext(app=None, operator="op")


async def test_preview_never_runs_executors_and_blocks_writes() -> None:
    ran: list = []

    async def executor(ctx, params):
        ran.append(params)
        return {}

    async def pre_tries_write(ctx, params):
        await ctx.call("POST", "/anything")
        return None

    reg = fresh_registry(
        make_spec("t.safe", executor, risk="local_safe"),
        make_spec("t.guarded", executor, risk="local_safe",
                  preconditions=(Precondition("w", "пишет", pre_tries_write),)))
    preview = await build_preview(_plan({"action_id": "t.safe", "params": {"n": 1}},
                                        {"action_id": "t.guarded", "params": {}}), reg, _ctx())
    assert ran == []
    assert preview["steps"][0]["valid"] is True
    assert preview["steps"][1]["valid"] is False
    assert "not allowed in read-only" in preview["steps"][1]["precondition_failures"][0]["reason"]
    assert preview["ok"] is False

    with pytest.raises(WriteNotAllowed):
        await _ctx().readonly_copy().call("DELETE", "/x")


async def test_preview_cost_risk_and_destructive_effects() -> None:
    async def ex(ctx, params):
        return {}

    reg = fresh_registry(
        make_spec("t.read", ex),
        make_spec("t.paid", ex, risk="paid", spends=True, cost=(10, 30)),
        make_spec("t.paid2", ex, risk="paid", spends=True, cost=(5, 5)),
        make_spec("t.kill", ex, risk="destructive", destructive=True,
                  destructive_effects=lambda p: "удалит всё"))
    preview = await build_preview(_plan(
        {"action_id": "t.read"}, {"action_id": "t.paid"}, {"action_id": "t.paid2"},
        {"action_id": "t.kill", "depends_on": [1]}), reg, _ctx())
    assert preview["ok"] and preview["needs_approval"]
    assert preview["risk_max"] == "destructive"
    assert preview["cost"] == {"low": 15.0, "high": 35.0, "unknown_steps": 0, "known": True}
    assert preview["destructive_effects"] == [
        {"index": 3, "action_id": "t.kill", "text": "удалит всё"}]


async def test_unknown_cost_is_not_zero() -> None:
    async def ex(ctx, params):
        return {}

    reg = fresh_registry(make_spec("t.unk", ex, risk="paid", spends=True))
    preview = await build_preview(_plan({"action_id": "t.unk"}), reg, _ctx())
    assert preview["cost"]["unknown_steps"] == 1 and preview["cost"]["known"] is False
    assert preview["steps"][0]["cost"]["known"] is False


async def test_invalid_params_unknown_action_and_bad_dependency() -> None:
    async def ex(ctx, params):
        return {}

    reg = fresh_registry(make_spec("t.a", ex))
    preview = await build_preview(_plan(
        {"action_id": "t.a", "params": {"n": -1}},
        {"action_id": "t.missing"},
        {"action_id": "t.a", "depends_on": [5]}), reg, _ctx())
    assert not preview["ok"]
    joined = " ".join(preview["errors"])
    assert "меньше допустимого (0)" in joined and "неизвестное действие" in joined
    assert preview["needs_approval"] is True


def test_plan_hash_is_deterministic_and_content_bound() -> None:
    async def ex(ctx, params):
        return {}

    reg = fresh_registry(make_spec("t.a", ex), make_spec("t.b", ex))
    p1 = _plan({"action_id": "t.a", "params": {"n": 1}}, {"action_id": "t.b"}, text="one")
    p2 = _plan({"action_id": "t.a", "params": {"n": 1}}, {"action_id": "t.b"}, text="two")
    assert plan_hash(p1, reg) == plan_hash(p2, reg)
    assert plan_hash(p1, reg) == plan_hash(p1, reg)
    changed = _plan({"action_id": "t.a", "params": {"n": 2}}, {"action_id": "t.b"})
    reordered = _plan({"action_id": "t.b"}, {"action_id": "t.a", "params": {"n": 1}})
    assert len({plan_hash(p1, reg), plan_hash(changed, reg), plan_hash(reordered, reg)}) == 3
    reg2 = fresh_registry(make_spec("t.a", ex, risk="local_safe"), make_spec("t.b", ex))
    assert plan_hash(p1, reg) != plan_hash(p1, reg2)


def test_plan_json_is_strict() -> None:
    for bad in (None, [], "x", [{"action_id": ""}], [{"action_id": "a.b", "approval": "yes"}],
                [{"action_id": "a.b", "params": []}], [{"action_id": "a.b", "depends_on": ["0"]}]):
        with pytest.raises(PlanFormatError):
            Plan.from_json(bad, max_steps=5)
    with pytest.raises(PlanFormatError, match="Слишком много"):
        Plan.from_json([{"action_id": "a.b"}] * 3, max_steps=2)
