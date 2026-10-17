"""Стоимость попытки пишется в spend ledger; исчерпание $10 — эскалация."""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.orchestrator.routing.config import Price
from app.orchestrator.routing.ledger import SpendLedger
from app.orchestrator.routing.model_router import LiveRouter
from app.orchestrator.routing.types import waiting_approval_status
from tests.routing.fakes import FakeTransport, ok


def _priced(config):
    prices = dict(config.prices)
    prices["gpt-5.6-sol"] = Price(prompt=1_000_000, completion=0, reasoning=0)
    return replace(config, prices=prices, internal_budget_usd=10)


async def test_cost_charges_ledger_and_escalates(config, sink):
    priced = _priced(config)
    transport = FakeTransport(
        [
            ok("first", prompt_tokens=6),
            ok("second", prompt_tokens=6),
            ok("third-should-not-run", prompt_tokens=1),
        ]
    )
    ledger = SpendLedger(internal_budget_usd=10)
    router = LiveRouter(priced, transport=transport, ledger=ledger, sink=sink)
    messages = [{"role": "user", "content": "суммируй"}]
    first = await router.call("strong", messages, run_id="run-a", agent_role="summarizer")
    assert first.cost_usd == 6
    assert first.approval is None
    assert first.task_status is None
    assert ledger.spent("run-a") == 6
    assert sink.records[-1].cost_usd == 6
    assert sink.records[-1].prompt_tokens == 6
    assert sink.records[-1].status == "ok"

    second = await router.call("strong", messages, run_id="run-a", task_id="13")
    assert second.text == "second"
    assert second.finish_reason == "stop"
    assert second.task_status == "waiting_approval"
    assert second.approval is not None
    assert second.approval.reason == "budget_exhausted"
    assert second.approval.task_id == 13
    assert second.approval.action == ""
    assert second.approval.risk == ""
    assert second.approval.change_id is None
    assert second.approval.payload["spent_usd"] == 12
    assert second.approval.payload["budget_usd"] == 10
    assert second.approval.payload["run_id"] == "run-a"
    assert ledger.spent("run-a") == 12
    assert sink.records[-1].status == "ok"

    third = await router.call("strong", messages, run_id="run-a", task_id="not-an-int")
    assert third.task_status == "waiting_approval"
    assert third.text == ""
    assert third.finish_reason == ""
    assert third.approval is not None
    assert third.approval.reason == "budget_exhausted"
    assert third.approval.task_id is None
    assert third.approval.payload["task_ref"] == "not-an-int"
    assert ledger.spent("run-a") == 12
    assert len(transport.calls) == 2
    assert len(sink.records) == 2
    assert all(row.status in {"ok", "timeout", "error", "fixture_miss"} for row in sink.records)
    assert "суммируй" not in repr(sink.records[-1])


async def test_other_run_is_not_blocked(config):
    priced = _priced(config)
    transport = FakeTransport([ok("a", prompt_tokens=10), ok("b", prompt_tokens=1)])
    router = LiveRouter(priced, transport=transport)
    await router.call("strong", [{"role": "user", "content": "x"}], run_id="run-a")
    other = await router.call("strong", [{"role": "user", "content": "y"}], run_id="run-b")
    assert other.text == "b"
    assert other.approval is None
    assert other.task_status is None


async def test_pipeline_scope_does_not_eat_internal_budget(config):
    priced = _priced(config)
    transport = FakeTransport([ok("p", prompt_tokens=100), ok("i", prompt_tokens=1)])
    ledger = SpendLedger(internal_budget_usd=10, pipeline_budget_usd=40)
    router = LiveRouter(priced, transport=transport, ledger=ledger)
    pipe = await router.call(
        "strong",
        [{"role": "user", "content": "x"}],
        run_id="run-p",
        budget_scope="pipeline",
    )
    assert pipe.text == "p"
    assert pipe.task_status == "waiting_approval"
    assert pipe.approval is not None
    assert pipe.approval.reason == "budget_exhausted"
    assert pipe.approval.payload["budget_usd"] == 40
    assert pipe.approval.payload["spent_usd"] == 100
    assert ledger.pipeline_spent("run-p") == 100
    assert ledger.spent("run-p") == 0
    assert ledger.pipeline_exhausted("run-p") is True
    assert ledger.exhausted("run-p") is False
    assert ledger.totals("run-p")["budget_exhausted"] is False
    internal = await router.call(
        "strong", [{"role": "user", "content": "y"}], run_id="run-p"
    )
    assert internal.text == "i"
    assert internal.approval is None
    assert ledger.spent("run-p") == 1


async def test_pipeline_cap_blocks_before_the_call(config):
    ledger = SpendLedger(internal_budget_usd=10, pipeline_budget_usd=40)
    ledger.hydrate("run-p", 0, 40)
    transport = FakeTransport([ok("still")])
    router = LiveRouter(config, transport=transport, ledger=ledger)
    blocked = await router.call(
        "strong",
        [{"role": "user", "content": "x"}],
        stream=True,
        run_id="run-p",
        budget_scope="pipeline",
    )
    assert blocked.text == ""
    assert blocked.task_status == "waiting_approval"
    assert blocked.approval is not None
    assert blocked.approval.reason == "budget_exhausted"
    assert blocked.approval.payload["budget_usd"] == 40
    assert blocked.approval.payload["spent_usd"] == 40
    assert transport.calls == []
    internal = await router.call(
        "strong",
        [{"role": "user", "content": "y"}],
        run_id="run-p",
    )
    assert internal.text == "still"
    assert internal.approval is None
    assert len(transport.calls) == 1


async def test_unknown_budget_scope_is_an_error(config):
    transport = FakeTransport([ok("x")])
    router = LiveRouter(config, transport=transport)
    with pytest.raises(ValueError, match=r"internal\|pipeline"):
        await router.call(
            "strong",
            [{"role": "user", "content": "да"}],
            run_id="run-x",
            budget_scope="external",
        )
    assert transport.calls == []


def test_charge_rejects_unknown_scope():
    ledger = SpendLedger()
    with pytest.raises(ValueError, match=r"internal\|pipeline"):
        ledger.charge("run-x", 1, scope="external")
    with pytest.raises(ValueError, match=r"internal\|pipeline"):
        ledger.charge(None, 1, scope="sideways")
    assert ledger.spent("run-x") == 0.0
    assert ledger.pipeline_spent("run-x") == 0.0


def test_missing_run_id_is_not_an_anonymous_bucket():
    ledger = SpendLedger(internal_budget_usd=10)
    assert ledger.spent(None) == 0.0
    assert ledger.exhausted(None) is False
    with pytest.raises(ValueError, match="internal budget requires run_id"):
        ledger.charge(None, 10, scope="internal")
    assert ledger.spent(None) == 0.0
    assert ledger.exhausted("later") is False


def test_hydrate_restores_pipeline_sum():
    ledger = SpendLedger(internal_budget_usd=10)
    ledger.hydrate("run-h", 4, 12)
    assert ledger.spent("run-h") == 4
    assert ledger.pipeline_spent("run-h") == 12
    view = ledger.totals("run-h")
    assert view["internal_spent_usd"] == 4
    assert view["pipeline_spent_usd"] == 12
    assert view["budget_exhausted"] is False
    with pytest.raises(ValueError, match="hydrate requires run_id"):
        ledger.hydrate(None, 1, 1)


async def test_classify_blocked_budget_is_explicit(config):
    ledger = SpendLedger(internal_budget_usd=10)
    ledger.hydrate("run-a", 10, 3)
    transport = FakeTransport([ok("no")])
    router = LiveRouter(config, transport=transport, ledger=ledger)
    decision = await router.classify("что думаешь?", run_id="run-a", task_id="13")
    assert decision.reason == "budget_exhausted"
    assert decision.task_status == waiting_approval_status()
    assert decision.approval is not None
    assert decision.approval.reason == "budget_exhausted"
    assert decision.approval.task_id == 13
    assert transport.calls == []


async def test_call_without_run_id_raises(config):
    router = LiveRouter(config, transport=FakeTransport([ok("x")]))
    with pytest.raises(ValueError, match="internal budget requires run_id"):
        await router.call("strong", [{"role": "user", "content": "да"}])
