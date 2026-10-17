"""Публичное чтение SpendLedger: pipeline не входит в $10."""

from __future__ import annotations

import threading
from dataclasses import replace

from app.orchestrator.routing.config import Price
from app.orchestrator.routing.ledger import (
    SpendLedger,
    get_spend_ledger,
    reset_spend_ledger,
)
from app.orchestrator.routing.model_router import LiveRouter
from tests.routing.fakes import FakeTransport, ok


def test_unknown_run_is_zero_and_pipeline_skips_internal_cap():
    ledger = SpendLedger(internal_budget_usd=10)
    assert ledger.totals(None) == {
        "internal_spent_usd": 0.0,
        "pipeline_spent_usd": 0.0,
        "internal_limit_usd": 10.0,
        "internal_remaining_usd": 10.0,
        "budget_exhausted": False,
    }
    assert ledger.totals("missing")["internal_spent_usd"] == 0.0
    assert ledger.totals("missing")["budget_exhausted"] is False

    ledger.charge("run-1", 4, scope="internal")
    ledger.charge("run-1", 100, scope="pipeline")
    view = ledger.totals("run-1")
    assert view["internal_spent_usd"] == 4
    assert view["pipeline_spent_usd"] == 100
    assert view["internal_limit_usd"] == 10
    assert view["internal_remaining_usd"] == 6
    assert view["budget_exhausted"] is False
    assert ledger.spent("run-1") == 4
    assert ledger.internal_budget_usd == 10
    assert ledger.exhausted("run-1") is False
    assert ledger.pipeline_spent("run-1") == 100
    assert ledger.pipeline_spent(None) == 0.0
    assert ledger.pipeline_spent("") == 0.0
    assert ledger.pipeline_spent("missing") == 0.0
    assert ledger.read_pipeline_spent(project_id=1, run_id="run-1") == 100
    assert ledger.read_pipeline_spent(project_id=1, run_id=None) == 0.0
    assert _guard_pipeline(ledger, project_id=7, run_id="run-1") == 100
    assert _guard_pipeline(ledger, project_id=7, run_id="missing") == 0.0

    ledger.charge("run-1", 6, scope="internal")
    done = ledger.totals("run-1")
    assert done["internal_spent_usd"] == 10
    assert done["pipeline_spent_usd"] == 100
    assert done["internal_remaining_usd"] == 0
    assert done["budget_exhausted"] is True
    assert ledger.totals("")["internal_spent_usd"] == 0.0


def test_totals_are_thread_safe():
    ledger = SpendLedger(internal_budget_usd=10)

    def worker() -> None:
        for _ in range(25):
            ledger.charge("run-t", 1, scope="internal")
            ledger.charge("run-t", 2, scope="pipeline")

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    view = ledger.totals("run-t")
    assert view["internal_spent_usd"] == 100
    assert view["pipeline_spent_usd"] == 200
    assert view["budget_exhausted"] is True
    assert ledger.pipeline_spent("run-t") == 200
    assert ledger.read_pipeline_spent(project_id=1, run_id="run-t") == 200


async def test_router_without_instance_is_visible_to_getter(config):
    prices = dict(config.prices)
    prices["gpt-5.6-sol"] = Price(prompt=1_000_000, completion=0, reasoning=0)
    priced = replace(config, prices=prices)
    reset_spend_ledger()
    router = LiveRouter(priced, transport=FakeTransport([ok("x", prompt_tokens=3), ok("y", prompt_tokens=9)]))
    await router.call(
        "strong",
        [{"role": "user", "content": "суммируй"}],
        run_id="run-pub",
        budget_scope="internal",
    )
    await router.call(
        "strong",
        [{"role": "user", "content": "кадр"}],
        run_id="run-pub",
        budget_scope="pipeline",
    )
    same = get_spend_ledger()
    assert same is router.ledger
    view = same.totals("run-pub")
    assert view["internal_spent_usd"] == 3
    assert view["pipeline_spent_usd"] == 9
    assert view["budget_exhausted"] is False
    assert same.spent("run-pub") == 3
    assert same.pipeline_spent("run-pub") == 9
    assert same.read_pipeline_spent(project_id=13, run_id="run-pub") == 9
    assert same.totals("other")["internal_spent_usd"] == 0.0
    assert same.pipeline_spent("other") == 0.0


def _guard_pipeline(source: object, *, project_id: int, run_id: str | None) -> float:
    """Тот же порядок, что ``read_pipeline_spent`` в #351 ``d37c63e4``."""
    for name in ("read_pipeline_spent", "pipeline_spent"):
        fn = getattr(source, name, None)
        if not callable(fn):
            continue
        try:
            return float(fn(project_id=project_id, run_id=run_id))
        except TypeError:
            return float(fn(run_id))
    raise AssertionError("публичного pipeline-геттера нет")
