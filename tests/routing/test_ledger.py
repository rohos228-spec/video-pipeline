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
    assert first.escalation is None
    assert ledger.spent("run-a") == 6
    assert sink.records[-1].cost_usd == 6
    assert sink.records[-1].prompt_tokens == 6
    assert sink.records[-1].status == "ok"

    second = await router.call("strong", messages, run_id="run-a")
    assert second.text == "second"
    assert second.escalation is not None
    assert second.escalation.approval_needed is True
    assert second.escalation.reason == "internal_budget_exhausted"
    assert second.finish_reason == "stop"
    assert ledger.spent("run-a") == 12

    third = await router.call("strong", messages, run_id="run-a")
    assert third.finish_reason == "escalation"
    assert third.text == ""
    assert third.escalation is not None
    assert ledger.spent("run-a") == 12
    assert len(transport.calls) == 2
    assert sink.records[-1].status == "error"
    assert sink.records[-1].error == "internal_budget_exhausted"
    assert "суммируй" not in repr(sink.records[-1])


async def test_other_run_is_not_blocked(config):
    priced = _priced(config)
    transport = FakeTransport([ok("a", prompt_tokens=10), ok("b", prompt_tokens=1)])
    router = LiveRouter(priced, transport=transport)
    await router.call("strong", [{"role": "user", "content": "x"}], run_id="run-a")
    other = await router.call("strong", [{"role": "user", "content": "y"}], run_id="run-b")
    assert other.text == "b"
    assert other.escalation is None


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
    assert pipe.escalation is None
    internal = await router.call(
        "strong", [{"role": "user", "content": "y"}], run_id="run-p"
    )
    assert internal.text == "i"
    assert internal.escalation is None
