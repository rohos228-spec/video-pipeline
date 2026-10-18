"""Реестр, shim ValidatorResult и зарезервированный vo_length."""

from __future__ import annotations

import sys
import types

import pytest
from pydantic import BaseModel, ConfigDict, Field

from app.orchestrator.validators import (
    VALIDATOR_CONTEXT_DRY_RUN,
    registered_names,
    reset_contract_cache,
    run,
    using_contracts,
    vo_length_available,
)
from app.orchestrator.validators import result as result_mod
from app.orchestrator.validators.spend_guard import set_spend_reader

_VO_LENGTH = "app.orchestrator.validators.vo_length"


def _hide_vo_length(monkeypatch: pytest.MonkeyPatch) -> None:
    """``None`` в sys.modules: импорт останавливается, файл на диске не читается."""

    monkeypatch.setitem(sys.modules, _VO_LENGTH, None)


class _QuietSpend:
    def read_totals(self, *, project_id: int, run_id: str | None = None) -> dict[str, float]:
        del project_id, run_id
        return {
            "daily_spent_usd": 0.0,
            "project_spent_usd": 0.0,
            "run_spent_usd": 0.0,
            "internal_spent_usd": 0.0,
            "actions_spent_usd": 0.0,
        }


def test_package_import_does_not_require_vo_length(monkeypatch: pytest.MonkeyPatch) -> None:
    _hide_vo_length(monkeypatch)
    assert "vo_length" in registered_names()
    assert vo_length_available() is False
    assert sys.modules[_VO_LENGTH] is None


def test_vo_length_by_name_is_unavailable_not_an_import_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hide_vo_length(monkeypatch)
    result = run("vo_length", 1, {})
    assert result.name == "vo_length"
    assert result.passed is False
    assert result.blocking is False
    assert result.details["reason"] == "unavailable"


def test_all_ignores_missing_vo_length(monkeypatch: pytest.MonkeyPatch) -> None:
    _hide_vo_length(monkeypatch)
    set_spend_reader(_QuietSpend())
    try:
        result = run("all", 1, {VALIDATOR_CONTEXT_DRY_RUN: True, "run_id": "all-dry"})
    finally:
        set_spend_reader(None)
    assert result.name == "all"
    assert result.passed is True
    assert result.blocking is False
    assert result.details["unavailable"] == ["vo_length"]
    assert result.details["dry_run"] is True
    dumped = result.model_dump(by_alias=True)
    assert dumped["pass"] is True
    names = [item["name"] for item in dumped["details"]["results"]]
    assert names == list(registered_names())


def test_all_runs_vo_length_when_module_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Фейковый vo_length с check_vo_lengths входит в прогон all, не в unavailable."""

    seen: list[list[str]] = []
    module = types.ModuleType(_VO_LENGTH)

    def check_vo_lengths(fragments: list[str]) -> list[str]:
        seen.append(list(fragments))
        return []

    module.check_vo_lengths = check_vo_lengths
    monkeypatch.setitem(sys.modules, _VO_LENGTH, module)
    set_spend_reader(_QuietSpend())
    try:
        result = run("all", 1, {VALIDATOR_CONTEXT_DRY_RUN: True, "run_id": "all-vo"})
    finally:
        set_spend_reader(None)
    assert seen == [[]]
    assert "vo_length" not in result.details["unavailable"]
    vo = next(item for item in result.details["results"] if item["name"] == "vo_length")
    assert vo["pass"] is True
    assert vo["details"]["available"] is True
    assert result.passed is True


def test_unknown_validator() -> None:
    result = run("not_a_validator", 1, {})
    assert result.details["reason"] == "unknown_validator"
    assert result.blocking is False


def test_checker_exception_is_blocking_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(project_id: int, context: dict) -> None:
        raise RuntimeError("boom")

    monkeypatch.setitem(
        sys.modules["app.orchestrator.validators"]._CHECKS,
        "no_deletions",
        boom,
    )
    result = run("no_deletions", 1, {"before": {}, "after": {}})
    assert result.details["reason"] == "validator_error"
    assert result.details["error"] == "RuntimeError"
    assert result.blocking is True


def test_vo_length_adapter_uses_check_vo_lengths(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("app.orchestrator.validators.vo_length")

    def check_vo_lengths(fragments: list[str]) -> list[dict[str, str]]:
        return [{"id": "too-long"}] if any(len(item) > 300 for item in fragments) else []

    module.check_vo_lengths = check_vo_lengths
    monkeypatch.setitem(sys.modules, "app.orchestrator.validators.vo_length", module)
    bad = run("vo_length", 1, {"fragments": ["x" * 301]})
    assert bad.passed is False
    assert bad.blocking is True
    assert bad.details["available"] is True
    ok = run("vo_length", 1, {"fragments": ["ok"]})
    assert ok.passed is True


def test_vo_length_check_callable_is_preferred(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("app.orchestrator.validators.vo_length")

    def check(project_id: int, context: dict) -> object:
        from app.orchestrator.validators.result import make_result

        return make_result(name="vo_length", passed=True, blocking=False, details={"project_id": project_id})

    module.check = check
    monkeypatch.setitem(sys.modules, "app.orchestrator.validators.vo_length", module)
    result = run("vo_length", 15, {})
    assert result.passed is True
    assert result.details["project_id"] == 15


def test_shim_switches_to_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    class ContractsResult(BaseModel):
        model_config = ConfigDict(populate_by_name=True)

        name: str = ""
        passed: bool = Field(default=False, alias="pass", serialization_alias="pass")
        details: object = ""
        blocking: bool = False

    module = types.ModuleType("app.orchestrator.contracts")
    module.ValidatorResult = ContractsResult
    module.VALIDATOR_CONTEXT_DRY_RUN = "dry_run"
    monkeypatch.setitem(sys.modules, "app.orchestrator.contracts", module)
    reset_contract_cache()
    try:
        assert using_contracts() is True
        assert result_mod.validator_result_type() is ContractsResult
        result = run("no_deletions", 1, {})
        assert isinstance(result, ContractsResult)
        assert result.model_dump(by_alias=True)["pass"] is True
    finally:
        reset_contract_cache()


def test_pass_alias_on_shim() -> None:
    reset_contract_cache()
    result = run("plan_sanity", 1, {})
    assert result.passed is True
    assert result.model_dump(by_alias=True)["pass"] is True
    assert VALIDATOR_CONTEXT_DRY_RUN == "dry_run"
