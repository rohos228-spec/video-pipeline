"""Стоимость попытки пишется в spend ledger; исчерпание $10 — эскалация."""

from __future__ import annotations

from dataclasses import replace

from app.orchestrator.routing.config import Price
from app.orchestrator.routing.model_router import LiveRouter
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
    from app.orchestrator.routing.ledger import SpendLedger

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
    router = LiveRouter(priced, transport=transport)
    pipe = await router.call(
        "strong",
        [{"role": "user", "content": "x"}],
        run_id="run-p",
        budget_scope="pipeline",
    )
    assert pipe.approval is None
    internal = await router.call(
        "strong", [{"role": "user", "content": "y"}], run_id="run-p"
    )
    assert internal.text == "i"
    assert internal.approval is None
