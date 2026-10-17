"""spend_guard читает SpendLedger и action layer, свой журнал не ведёт."""

from __future__ import annotations

import sys
import types

import pytest

from app.orchestrator.validators import VALIDATOR_CONTEXT_DRY_RUN, run
from app.orchestrator.validators.spend_guard import get_spend_reader, set_spend_reader


def _totals(**overrides: float) -> dict[str, float]:
    base = {
        "daily_spent_usd": 0.0,
        "project_spent_usd": 0.0,
        "run_spent_usd": 0.0,
        "internal_spent_usd": 0.0,
        "actions_spent_usd": 0.0,
    }
    base.update(overrides)
    return base


class _Reader:
    def __init__(self, totals: dict[str, float]) -> None:
        self.totals = totals
        self.reads: list[tuple[int, str | None]] = []
        self.charges = 0

    def read_totals(self, *, project_id: int, run_id: str | None = None) -> dict[str, float]:
        self.reads.append((project_id, run_id))
        return dict(self.totals)

    def charge(self, amount: float) -> None:
        self.charges += 1
        self.totals["internal_spent_usd"] += amount


def test_internal_budget_exhausted_is_not_a_hard_block() -> None:
    result = run("spend_guard", 3, {"spend": _totals(internal_spent_usd=10.0), "run_id": "run-1"})
    assert result.passed is False
    assert result.blocking is False
    assert result.details["reason"] == "budget_exhausted"
    assert result.details["violations"][0]["cap_usd"] == 10.0


def test_forecast_exhausts_internal_sub_budget() -> None:
    result = run(
        "spend_guard",
        3,
        {"spend": _totals(internal_spent_usd=9.0), "forecast_usd": {"internal": 1.0}},
    )
    assert result.details["reason"] == "budget_exhausted"
    assert result.blocking is False


def test_hard_caps_block() -> None:
    daily = run("spend_guard", 1, {"spend": _totals(daily_spent_usd=200.01)})
    assert daily.blocking is True
    assert daily.details["reason"] == "spend_cap_daily"
    project = run("spend_guard", 1, {"spend": _totals(project_spent_usd=100.01)})
    assert project.details["reason"] == "spend_cap_project"
    run_cap = run("spend_guard", 1, {"spend": _totals(run_spent_usd=50.01)})
    assert run_cap.details["reason"] == "spend_cap_run"
    actions = run("spend_guard", 1, {"spend": _totals(actions_spent_usd=40.01)})
    assert actions.details["reason"] == "actions_sub_budget"


def test_exact_hard_cap_still_passes_and_internal_does_not() -> None:
    ok = run(
        "spend_guard",
        1,
        {"spend": _totals(daily_spent_usd=200, project_spent_usd=100, run_spent_usd=50, actions_spent_usd=40)},
    )
    assert ok.passed is True
    exhausted = run("spend_guard", 1, {"spend": _totals(internal_spent_usd=10)})
    assert exhausted.details["reason"] == "budget_exhausted"


def test_disabled_cap_zero_does_not_fire() -> None:
    result = run(
        "spend_guard",
        1,
        {"spend": {**_totals(internal_spent_usd=50, daily_spent_usd=999), "internal_cap_usd": 0, "daily_cap_usd": 0}},
    )
    assert result.passed is True


def test_reader_is_mocked_and_dry_run_does_not_charge() -> None:
    reader = _Reader(_totals(internal_spent_usd=3))
    snapshot = dict(reader.totals)
    result = run(
        "spend_guard",
        8,
        {"spend_reader": reader, "run_id": "r", VALIDATOR_CONTEXT_DRY_RUN: True, "forecast_usd": {"actions": 1}},
    )
    assert result.passed is True
    assert reader.reads == [(8, "r")]
    assert reader.charges == 0
    assert reader.totals == snapshot


def test_callable_reader_and_injected_reader() -> None:
    def read(*, project_id: int, run_id: str | None = None) -> dict[str, float]:
        assert project_id == 2
        assert run_id is None
        return _totals(run_spent_usd=51)

    via_context = run("spend_guard", 2, {"spend_reader": read})
    assert via_context.details["reason"] == "spend_cap_run"
    reader = _Reader(_totals(project_spent_usd=101))
    set_spend_reader(reader)
    try:
        via_global = run("spend_guard", 5, {})
    finally:
        set_spend_reader(None)
    assert via_global.blocking is True
    assert reader.reads == [(5, None)]


def test_missing_snapshot_does_not_invent_spend() -> None:
    result = run("spend_guard", 1, {VALIDATOR_CONTEXT_DRY_RUN: True})
    assert result.passed is True
    assert result.details["reason"] == "no_spend_snapshot"
    assert get_spend_reader() is None


def test_internal_and_hard_cap_keep_budget_exhausted_reason() -> None:
    result = run(
        "spend_guard",
        1,
        {"spend": _totals(internal_spent_usd=10, daily_spent_usd=201), "forecast_usd": {"daily": 1}},
    )
    assert result.details["reason"] == "budget_exhausted"
    assert "spend_cap_daily" in result.details["reasons"]
    assert result.blocking is True


class _Ledger:
    """Срез SpendLedger: charge только для проверки, что валидатор его не зовёт."""

    def __init__(self, spent_usd: float, budget: float = 10.0, pipeline_usd: float = 0.0) -> None:
        self.internal_budget_usd = budget
        self._internal = spent_usd
        self._pipeline = pipeline_usd
        self.charge_calls = 0
        self.seen: list[str | None] = []

    def spent(self, run_id: str | None) -> float:
        self.seen.append(run_id)
        return self._internal

    def charge(self, run_id: str | None, cost: float, *, scope: str = "internal") -> None:
        self.charge_calls += 1
        if scope == "pipeline":
            self._pipeline += cost
        else:
            self._internal += cost


class _Status:
    def __init__(self, value: str) -> None:
        self.value = value


class _Approval:
    def __init__(self, reason: str, payload: dict | None = None) -> None:
        self.reason = reason
        self.payload = payload or {}


class _Response:
    def __init__(self, status: object, approval: object) -> None:
        self.task_status = status
        self.approval = approval


def test_ledger_reports_spent_and_remaining_without_charging() -> None:
    ledger = _Ledger(4.0, pipeline_usd=40.0)
    result = run(
        "spend_guard",
        3,
        {"spend_ledger": ledger, "run_id": "run-9", VALIDATOR_CONTEXT_DRY_RUN: True},
    )
    assert result.passed is True
    assert ledger.charge_calls == 0
    assert ledger.seen == ["run-9"]
    assert ledger._pipeline == 40.0
    internal = result.details["internal"]
    assert internal["spent_usd"] == 4.0
    assert internal["remaining_usd"] == 6.0
    assert internal["budget_usd"] == 10.0
    assert internal["source"] == "spend_ledger"
    assert "daily_spent_usd" not in result.details["totals"]


def test_ledger_internal_exhaustion_stays_non_blocking() -> None:
    ledger = _Ledger(10.0)
    result = run("spend_guard", 3, {"ledger": ledger, "run_id": "run-9"})
    assert result.passed is False
    assert result.blocking is False
    assert result.details["reason"] == "budget_exhausted"
    assert result.details["internal"]["remaining_usd"] == 0.0
    assert ledger.charge_calls == 0


def test_ledger_overrides_action_layer_internal_spend() -> None:
    ledger = _Ledger(2.5)
    reader = _Reader(_totals(internal_spent_usd=9.5, actions_spent_usd=3.0))
    result = run(
        "spend_guard",
        4,
        {"spend_ledger": ledger, "spend_reader": reader, "run_id": "r2"},
    )
    assert result.passed is True
    assert result.details["internal"]["spent_usd"] == 2.5
    assert result.details["internal"]["remaining_usd"] == 7.5
    assert result.details["totals"]["actions_spent_usd"] == 3.0
    assert result.details["sources"] == ["action_layer", "spend_ledger"]
    assert reader.reads == [(4, "r2")]
    assert reader.charges == 0


def test_router_budget_exhausted_response_blocks() -> None:
    response = _Response(_Status("waiting_approval"), _Approval("budget_exhausted", {"spent_usd": 10, "budget_usd": 10, "run_id": "r"}))
    result = run("spend_guard", 1, {"response": response, "run_id": "r"})
    assert result.passed is False
    assert result.blocking is True
    assert result.details["reason"] == "budget_exhausted"
    assert result.details["response"]["task_status"] == "waiting_approval"
    assert result.details["response"]["approval_reason"] == "budget_exhausted"
    assert result.details["violations"][0]["source"] == "llm_response"
    assert result.details["violations"][0]["spent_usd"] == 10.0
    assert result.details["violations"][0]["remaining_usd"] == 0.0


def test_router_response_blocks_even_when_ledger_still_has_room() -> None:
    ledger = _Ledger(1.0)
    response = {
        "task_status": "waiting_approval",
        "approval": {"reason": "budget_exhausted", "payload": {"spent_usd": 10, "budget_usd": 10}},
    }
    result = run("spend_guard", 1, {"llm_response": response, "spend_ledger": ledger, "run_id": 7})
    assert result.passed is False
    assert result.blocking is True
    assert result.details["internal"]["spent_usd"] == 1.0
    assert result.details["internal"]["remaining_usd"] == 9.0
    assert result.details["violations"][0]["source"] == "llm_response"
    assert ledger.seen == ["7"]
    assert ledger.charge_calls == 0


def test_other_approval_reason_is_not_budget_exhausted() -> None:
    response = {"task_status": "waiting_approval", "approval": {"reason": "risk"}}
    result = run("spend_guard", 1, {"response": response})
    assert result.passed is True
    assert result.details["reason"] == "no_spend_snapshot"


def test_contracts_predicate_accepts_router_response(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("app.orchestrator.contracts")

    def budget_exhausted_response(response: object) -> bool:
        return getattr(response, "task_status", None) == "waiting_approval" and getattr(
            getattr(response, "approval", None), "reason", ""
        ) == "budget_exhausted"

    module.budget_exhausted_response = budget_exhausted_response
    monkeypatch.setitem(sys.modules, "app.orchestrator.contracts", module)
    response = _Response("waiting_approval", _Approval("budget_exhausted"))
    result = run("spend_guard", 1, {"response": response})
    assert result.blocking is True
    assert result.passed is False


def test_garbage_spend_numbers_do_not_count() -> None:
    result = run(
        "spend_guard",
        1,
        {
            "spend": {
                "daily_spent_usd": True,
                "project_spent_usd": "нет",
                "run_spent_usd": float("nan"),
                "internal_spent_usd": -3,
                "actions_spent_usd": float("inf"),
            }
        },
    )
    assert result.passed is True
    assert result.details["totals"]["internal_spent_usd"] == 0.0


def test_unreadable_sources_are_ignored() -> None:
    class Opaque:
        pass

    assert run("spend_guard", 1, {"spend": "нет"}).details["reason"] == "no_spend_snapshot"
    assert run("spend_guard", 1, {"spend_reader": Opaque()}).details["reason"] == "no_spend_snapshot"
    assert run("spend_guard", 1, {"spend_ledger": Opaque()}).details["reason"] == "no_spend_snapshot"

    class NoArg:
        internal_budget_usd = None

        def spent(self) -> float:
            return 10.0

    numeric = run("spend_guard", 1, {"ledger": NoArg()})
    assert numeric.details["reason"] == "budget_exhausted"
    assert numeric.blocking is False

    class BadReader:
        def read_totals(self, *, project_id: int, run_id: str | None = None) -> str:
            return "нет"

    assert run("spend_guard", 1, {"spend_reader": BadReader()}).details["reason"] == "no_spend_snapshot"
