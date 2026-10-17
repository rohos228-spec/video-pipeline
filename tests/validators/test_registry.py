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


def test_package_import_does_not_require_vo_length() -> None:
    assert "vo_length" in registered_names()
    assert vo_length_available() is False
    assert "app.orchestrator.validators.vo_length" not in sys.modules


def test_vo_length_by_name_is_unavailable_not_an_import_error() -> None:
    result = run("vo_length", 1, {})
    assert result.name == "vo_length"
    assert result.passed is False
    assert result.blocking is False
    assert result.details["reason"] == "unavailable"


def test_all_ignores_missing_vo_length() -> None:
    result = run("all", 1, {VALIDATOR_CONTEXT_DRY_RUN: True})
    assert result.name == "all"
    assert result.passed is True
    assert result.blocking is False
    assert result.details["unavailable"] == ["vo_length"]
    assert result.details["dry_run"] is True
    dumped = result.model_dump(by_alias=True)
    assert dumped["pass"] is True
    names = [item["name"] for item in dumped["details"]["results"]]
    assert names == list(registered_names())


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
