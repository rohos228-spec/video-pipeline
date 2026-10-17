"""spend_guard читает SpendLedger и action layer, свой журнал не ведёт."""

from __future__ import annotations

import sys
import types

import pytest

from app.orchestrator.validators import VALIDATOR_CONTEXT_DRY_RUN, run
from app.orchestrator.validators.result import validator_budget_exhausted
from app.orchestrator.validators.spend_guard import check, get_spend_reader, set_spend_reader


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


def test_budget_exhausted_blocks_with_reason() -> None:
    numeric = run("spend_guard", 3, {"spend": _totals(internal_spent_usd=10.0), "run_id": "run-1"})
    assert numeric.passed is False
    assert numeric.blocking is True
    assert numeric.details["reason"] == "budget_exhausted"
    assert numeric.details["violations"][0]["cap_usd"] == 10.0
    signaled = run(
        "spend_guard",
        3,
        {
            "response": {
                "task_status": "waiting_approval",
                "approval": {"reason": "budget_exhausted"},
            },
            "run_id": "run-1",
        },
    )
    assert signaled.passed is False
    assert signaled.blocking is True
    assert signaled.details["reason"] == "budget_exhausted"


def test_forecast_exhausts_internal_sub_budget() -> None:
    result = run(
        "spend_guard",
        3,
        {"spend": _totals(internal_spent_usd=9.0), "forecast_usd": {"internal": 1.0}, "run_id": "run-1"},
    )
    assert result.passed is False
    assert result.details["reason"] == "budget_exhausted"
    assert result.blocking is True


def test_hard_caps_block() -> None:
    daily = run("spend_guard", 1, {"spend": _totals(daily_spent_usd=200.01), "run_id": "run-1"})
    assert daily.blocking is True
    assert daily.details["reason"] == "spend_cap_daily"
    project = run("spend_guard", 1, {"spend": _totals(project_spent_usd=100.01), "run_id": "run-1"})
    assert project.details["reason"] == "spend_cap_project"
    run_cap = run("spend_guard", 1, {"spend": _totals(run_spent_usd=50.01), "run_id": "run-1"})
    assert run_cap.details["reason"] == "spend_cap_run"
    actions = run("spend_guard", 1, {"spend": _totals(actions_spent_usd=40.01), "run_id": "run-1"})
    assert actions.details["reason"] == "actions_sub_budget"


def test_exact_hard_cap_still_passes_and_internal_does_not() -> None:
    ok = run(
        "spend_guard",
        1,
        {
            "spend": _totals(daily_spent_usd=200, project_spent_usd=100, run_spent_usd=50, actions_spent_usd=40),
            "run_id": "run-1",
        },
    )
    assert ok.passed is True
    exhausted = run("spend_guard", 1, {"spend": _totals(internal_spent_usd=10), "run_id": "run-1"})
    assert exhausted.passed is False
    assert exhausted.blocking is True
    assert exhausted.details["reason"] == "budget_exhausted"


def test_disabled_cap_zero_does_not_fire() -> None:
    result = run(
        "spend_guard",
        1,
        {
            "spend": {**_totals(internal_spent_usd=50, daily_spent_usd=999), "internal_cap_usd": 0, "daily_cap_usd": 0},
            "run_id": "run-1",
        },
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
        assert run_id == "run-2"
        return _totals(run_spent_usd=51)

    via_context = run("spend_guard", 2, {"spend_reader": read, "run_id": "run-2"})
    assert via_context.details["reason"] == "spend_cap_run"
    reader = _Reader(_totals(project_spent_usd=101))
    set_spend_reader(reader)
    try:
        via_global = run("spend_guard", 5, {"run_id": "run-5"})
    finally:
        set_spend_reader(None)
    assert via_global.blocking is True
    assert reader.reads == [(5, "run-5")]


def test_missing_snapshot_does_not_invent_spend() -> None:
    result = run("spend_guard", 1, {VALIDATOR_CONTEXT_DRY_RUN: True})
    assert result.passed is True
    assert result.details["reason"] == "no_spend_snapshot"
    assert get_spend_reader() is None


def test_internal_and_hard_cap_keep_budget_exhausted_reason() -> None:
    result = run(
        "spend_guard",
        1,
        {
            "spend": _totals(internal_spent_usd=10, daily_spent_usd=201),
            "forecast_usd": {"daily": 1},
            "run_id": "run-1",
        },
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
    assert result.details["pipeline_scope_spent_usd"] is None
    assert "daily_spent_usd" not in result.details["totals"]


def test_ledger_internal_exhaustion_blocks() -> None:
    ledger = _Ledger(10.0)
    result = run("spend_guard", 3, {"ledger": ledger, "run_id": "run-9"})
    assert result.passed is False
    assert result.blocking is True
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
    result = run("spend_guard", 1, {"response": response, "run_id": "run-1"})
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
    result = run("spend_guard", 1, {"response": response, "run_id": "run-1"})
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
            },
            "run_id": "run-1",
        },
    )
    assert result.passed is True
    assert result.details["totals"]["internal_spent_usd"] == 0.0


def test_unreadable_sources_are_ignored() -> None:
    class Opaque:
        pass

    assert run("spend_guard", 1, {"spend": "нет", "run_id": "run-1"}).details["reason"] == "no_spend_snapshot"
    assert run("spend_guard", 1, {"spend_reader": Opaque(), "run_id": "run-1"}).details["reason"] == "no_spend_snapshot"
    assert run("spend_guard", 1, {"spend_ledger": Opaque(), "run_id": "run-1"}).details["reason"] == "no_spend_snapshot"

    class NoArg:
        internal_budget_usd = None

        def spent(self) -> float:
            return 10.0

    numeric = run("spend_guard", 1, {"ledger": NoArg(), "run_id": "run-1"})
    assert numeric.details["reason"] == "budget_exhausted"
    assert numeric.blocking is True

    class BadReader:
        def read_totals(self, *, project_id: int, run_id: str | None = None) -> str:
            return "нет"

    assert run("spend_guard", 1, {"spend_reader": BadReader(), "run_id": "run-1"}).details["reason"] == "no_spend_snapshot"


def test_private_pipeline_field_is_not_read() -> None:
    class Hidden:
        internal_budget_usd = 10.0

        @property
        def _pipeline(self) -> dict[str, float]:
            raise AssertionError("приватное поле SpendLedger._pipeline")

        def spent(self, run_id: str | None) -> float:
            return 1.0

    result = run("spend_guard", 1, {"spend_ledger": Hidden(), "run_id": "r"})
    assert result.passed is True
    assert result.details["pipeline_scope_spent_usd"] is None


def test_optional_pipeline_spent_uses_actions_cap() -> None:
    class Ledger:
        internal_budget_usd = 10.0

        def spent(self, run_id: str | None) -> float:
            return 0.0

        def pipeline_spent(self, run_id: str | None) -> float:
            assert run_id == "r"
            return 40.01

    result = run("spend_guard", 1, {"ledger": Ledger(), "run_id": "r"})
    assert result.passed is False
    assert result.blocking is True
    assert result.details["reason"] == "actions_sub_budget"
    assert result.details["pipeline_scope_spent_usd"] == 40.01
    assert result.details["totals"]["actions_spent_usd"] == 40.01


def test_ws2_actions_stay_when_pipeline_scope_is_also_present() -> None:
    class Reader:
        def read_totals(self, *, project_id: int, run_id: str | None = None) -> dict[str, float]:
            return _totals(actions_spent_usd=1.0)

        def read_pipeline_spent(self, *, project_id: int, run_id: str | None = None) -> float:
            return 50.0

    result = run("spend_guard", 1, {"spend_reader": Reader(), "run_id": "r"})
    assert result.details["totals"]["actions_spent_usd"] == 1.0
    assert result.details["pipeline_scope_spent_usd"] == 50.0
    assert result.blocking is True
    assert result.details["reason"] == "actions_sub_budget"
    assert any(item["source"] == "pipeline_scope" for item in result.details["violations"])


def test_budget_exhausted_is_local_blocking_and_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """blocking и details.reason. Функция из contracts не читается."""

    numeric = run("spend_guard", 1, {"spend": _totals(internal_spent_usd=10.0), "run_id": "r"})
    assert numeric.blocking is True
    assert numeric.details["reason"] == "budget_exhausted"
    assert validator_budget_exhausted(numeric) is True

    signaled = run(
        "spend_guard",
        1,
        {
            "response": {
                "task_status": "waiting_approval",
                "approval": {"reason": "budget_exhausted", "payload": {"spent_usd": 10}},
            },
            "run_id": "run-1",
        },
    )
    assert validator_budget_exhausted(signaled) is True

    hard = run("spend_guard", 1, {"spend": _totals(daily_spent_usd=200.01), "run_id": "run-1"})
    assert hard.blocking is True
    assert hard.details["reason"] != "budget_exhausted"
    assert validator_budget_exhausted(hard) is False

    class ObjectDetails:
        reason = "budget_exhausted"

    class ObjectResult:
        blocking = True
        details = ObjectDetails()

    assert validator_budget_exhausted(ObjectResult()) is True
    quiet = ObjectResult()
    quiet.blocking = False
    assert validator_budget_exhausted(quiet) is False

    missing = run("spend_guard", 1, {})
    assert missing.blocking is True
    assert missing.details["reason"] == "missing_run_id"
    assert validator_budget_exhausted(missing) is False

    module = types.ModuleType("app.orchestrator.contracts")

    def remote(result: object) -> bool:
        raise AssertionError(result)

    module.validator_budget_exhausted = remote
    monkeypatch.setitem(sys.modules, "app.orchestrator.contracts", module)
    assert validator_budget_exhausted(numeric) is True


def _blank_run(run_id: str | None) -> bool:
    return run_id is None or str(run_id).strip() == ""


class _ContractLedger:
    """Форма SpendLedger #345 ``eefc08b4``.

    ``spent(None)`` — 0. Внутренний вызов без ``run_id`` (``charge``) —
    ``ValueError``. ``totals(None)`` — нулевой снимок, не исключение.
    """

    def __init__(self, *, internal: float, pipeline: float, exhausted: bool) -> None:
        self.internal_budget_usd = 10.0
        self._internal = internal
        self._pipe = pipeline
        self._exhausted = exhausted
        self.charge_calls = 0
        self.totals_runs: list[str | None] = []

    @property
    def _pipeline(self) -> dict[str, float]:
        raise AssertionError("приватное поле")

    def spent(self, run_id: str | None) -> float:
        if _blank_run(run_id):
            return 0.0
        return self._internal

    def pipeline_spent(self, run_id: str | None) -> float:
        if _blank_run(run_id):
            return 0.0
        return self._pipe

    def read_pipeline_spent(
        self,
        *,
        project_id: int | None = None,
        run_id: str | None = None,
    ) -> float:
        del project_id
        if _blank_run(run_id):
            return 0.0
        return self._pipe

    def totals(self, run_id: str | None = None) -> dict[str, float | bool]:
        self.totals_runs.append(run_id)
        if _blank_run(run_id):
            limit = self.internal_budget_usd
            return {
                "internal_spent_usd": 0.0,
                "pipeline_spent_usd": 0.0,
                "internal_limit_usd": limit,
                "internal_remaining_usd": limit,
                "budget_exhausted": False,
            }
        remaining = self.internal_budget_usd - self._internal
        return {
            "internal_spent_usd": self._internal,
            "pipeline_spent_usd": self._pipe,
            "internal_limit_usd": self.internal_budget_usd,
            "internal_remaining_usd": remaining,
            "budget_exhausted": self._exhausted,
        }

    def charge(self, run_id: str | None = None, cost: float = 0.0, *, scope: str = "internal") -> None:
        del cost
        if _blank_run(run_id):
            if scope == "pipeline":
                raise ValueError("pipeline budget requires run_id")
            raise ValueError("internal budget requires run_id")
        self.charge_calls += 1


def test_contract_ledger_empty_run_matches_eefc08b4() -> None:
    ledger = _ContractLedger(internal=4.0, pipeline=80.0, exhausted=True)
    assert ledger.spent(None) == 0.0
    assert ledger.spent("") == 0.0
    assert ledger.spent("  ") == 0.0
    assert ledger.spent("run-x") == 4.0
    empty = ledger.totals(None)
    assert empty["internal_spent_usd"] == 0.0
    assert empty["pipeline_spent_usd"] == 0.0
    assert empty["budget_exhausted"] is False
    with pytest.raises(ValueError, match="run_id"):
        ledger.charge(None, 1.0)
    with pytest.raises(ValueError, match="run_id"):
        ledger.charge("   ", 1.0, scope="pipeline")
    assert ledger.charge_calls == 0


def test_contract_ledger_budget_exhausted_flag_blocks() -> None:
    ledger = _ContractLedger(internal=1.0, pipeline=80.0, exhausted=True)
    result = run("spend_guard", 4, {"spend_ledger": ledger, "run_id": "run-x"})
    assert ledger.charge_calls == 0
    assert ledger.totals_runs == ["run-x"]
    assert result.passed is False
    assert result.blocking is True
    assert result.details["reason"] == "budget_exhausted"
    assert result.details["internal"]["spent_usd"] == 1.0
    assert result.details["internal"]["remaining_usd"] == 9.0
    assert result.details["pipeline_scope_spent_usd"] == 80.0
    assert validator_budget_exhausted(result) is True


def test_contract_ledger_pipeline_is_outside_internal_limit() -> None:
    ledger = _ContractLedger(internal=1.0, pipeline=80.0, exhausted=False)
    result = run("spend_guard", 4, {"ledger": ledger, "run_id": "run-x"})
    assert result.details["reason"] != "budget_exhausted"
    assert "budget_exhausted" not in result.details["reasons"]
    assert result.details["internal"]["spent_usd"] == 1.0
    assert result.details["internal"]["remaining_usd"] == 9.0
    assert result.details["reason"] == "actions_sub_budget"
    assert result.blocking is True


def test_real_spend_ledger_is_read_via_get_spend_ledger() -> None:
    """Настоящий модуль #345. Пока его нет в main, тест пропускается."""

    pytest.importorskip("app.orchestrator.routing.ledger")
    from app.orchestrator.routing.ledger import SpendLedger, get_spend_ledger, reset_spend_ledger

    ledger = reset_spend_ledger(10.0)
    assert isinstance(ledger, SpendLedger)
    assert get_spend_ledger() is ledger
    try:
        ledger.charge("ws1-run", 10.0, scope="internal")
        ledger.charge("ws1-run", 80.0, scope="pipeline")
        exhausted = run("spend_guard", 1, {"run_id": "ws1-run"})
        assert exhausted.passed is False
        assert exhausted.blocking is True
        assert exhausted.details["reason"] == "budget_exhausted"
        assert exhausted.details["internal"]["spent_usd"] == 10.0
        assert exhausted.details["pipeline_scope_spent_usd"] == 80.0

        only_pipeline = reset_spend_ledger(10.0)
        only_pipeline.charge("pipe-only", 80.0, scope="pipeline")
        quiet = run("spend_guard", 1, {"run_id": "pipe-only"})
        assert quiet.details["internal"]["spent_usd"] == 0.0
        assert quiet.details["reason"] != "budget_exhausted"
    finally:
        reset_spend_ledger(10.0)


def test_pipeline_reader_without_arguments() -> None:
    class Ledger:
        internal_budget_usd = 10.0

        def spent(self, run_id: str | None) -> float:
            return 0.0

        def pipeline_spent(self) -> float:
            return 3.0

    result = run("spend_guard", 1, {"ledger": Ledger(), "run_id": "r"})
    assert result.passed is True
    assert result.details["pipeline_scope_spent_usd"] == 3.0


def test_context_pipeline_reader_without_snapshot() -> None:
    def read_pipeline_spent(*, project_id: int, run_id: str | None = None) -> float:
        assert project_id == 2
        assert run_id == "z"
        return 10.0

    result = run("spend_guard", 2, {"read_pipeline_spent": read_pipeline_spent, "run_id": "z"})
    assert result.passed is True
    assert result.details["totals"]["actions_spent_usd"] == 10.0
    assert result.details["pipeline_scope_spent_usd"] == 10.0


class _CallSpy:
    def __init__(self, *, internal: float = 10.0, exhausted: bool = True) -> None:
        self.calls = 0
        self._internal = internal
        self._exhausted = exhausted

    def totals(self, run_id: str | None = None) -> dict[str, float | bool]:
        self.calls += 1
        limit = 10.0
        return {
            "internal_spent_usd": self._internal,
            "pipeline_spent_usd": 0.0,
            "internal_limit_usd": limit,
            "internal_remaining_usd": limit - self._internal,
            "budget_exhausted": self._exhausted,
        }

    def spent(self, run_id: str | None) -> float:
        self.calls += 1
        return self._internal

    def pipeline_spent(self, run_id: str | None) -> float:
        self.calls += 1
        return 0.0


def test_missing_run_id_blocks_without_calling_ledger() -> None:
    contexts: list[dict] = [
        {},
        {"run_id": None},
        {"run_id": ""},
        {"run_id": "   "},
    ]
    for extra in contexts:
        ledger = _CallSpy()
        reader = _Reader(_totals(internal_spent_usd=10.0, run_spent_usd=51))
        result = run(
            "spend_guard",
            9,
            {"spend_ledger": ledger, "spend_reader": reader, "spend": _totals(internal_spent_usd=10.0), **extra},
        )
        assert result.passed is False
        assert result.blocking is True
        assert result.details == {"reason": "missing_run_id"}
        assert ledger.calls == 0
        assert reader.reads == []


def test_dry_run_without_run_id_keeps_previous_behavior() -> None:
    empty = run("spend_guard", 1, {VALIDATOR_CONTEXT_DRY_RUN: True})
    assert empty.passed is True
    assert empty.blocking is False
    assert empty.details["reason"] == "no_spend_snapshot"

    def read(*, project_id: int, run_id: str | None = None) -> dict[str, float]:
        assert project_id == 2
        assert run_id is None
        return _totals(run_spent_usd=51)

    via_reader = run("spend_guard", 2, {"spend_reader": read, VALIDATOR_CONTEXT_DRY_RUN: True})
    assert via_reader.passed is False
    assert via_reader.blocking is True
    assert via_reader.details["reason"] == "spend_cap_run"

    ledger = _CallSpy()
    exhausted = run("spend_guard", 1, {"spend_ledger": ledger, VALIDATOR_CONTEXT_DRY_RUN: True})
    assert ledger.calls == 1
    assert exhausted.passed is False
    assert exhausted.blocking is True
    assert exhausted.details["reason"] == "budget_exhausted"

    zeros = _ContractLedger(internal=10.0, pipeline=80.0, exhausted=True)
    quiet = run("spend_guard", 1, {"spend_ledger": zeros, VALIDATOR_CONTEXT_DRY_RUN: True})
    assert zeros.totals_runs == [None]
    assert zeros.spent(None) == 0.0
    assert quiet.passed is True
    assert quiet.details["reason"] == "ok"
    assert quiet.details["reason"] != "missing_run_id"


def test_with_run_id_keeps_previous_behavior() -> None:
    under = run("spend_guard", 1, {"spend": _totals(internal_spent_usd=1.0), "run_id": "keep"})
    assert under.passed is True
    assert under.blocking is False
    assert under.details["reason"] == "ok"
    assert under.details["internal"]["spent_usd"] == 1.0

    over = run("spend_guard", 1, {"spend": _totals(internal_spent_usd=10.0), "run_id": "keep"})
    assert over.passed is False
    assert over.blocking is True
    assert over.details["reason"] == "budget_exhausted"

    ledger = _CallSpy(internal=10.0, exhausted=True)
    from_ledger = run("spend_guard", 3, {"spend_ledger": ledger, "run_id": "keep"})
    assert ledger.calls == 1
    assert from_ledger.details["reason"] == "budget_exhausted"
    assert from_ledger.blocking is True


def test_ledger_run_id_value_error_blocks_instead_of_raising() -> None:
    class Refuses:
        def totals(self, run_id: str | None = None) -> dict[str, float]:
            raise ValueError("internal budget requires run_id")

        def spent(self, run_id: str | None) -> float:
            raise ValueError("internal budget requires run_id")

    result = run("spend_guard", 1, {"spend_ledger": Refuses(), "run_id": "run-x"})
    assert result.passed is False
    assert result.blocking is True
    assert result.details == {"reason": "missing_run_id"}

    dry = run("spend_guard", 1, {"spend_ledger": Refuses(), VALIDATOR_CONTEXT_DRY_RUN: True})
    assert dry.passed is False
    assert dry.blocking is True
    assert dry.details == {"reason": "missing_run_id"}

    class Other:
        def totals(self, run_id: str | None = None) -> dict[str, float]:
            raise ValueError("disk full")

    with pytest.raises(ValueError, match="disk full"):
        check(1, {"spend_ledger": Other(), "run_id": "run-x"})
