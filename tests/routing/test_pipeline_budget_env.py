"""Потолок pipeline: ORCH_PIPELINE_BUDGET_USD, дефолт 40, мусор — ошибка."""

from __future__ import annotations

import pytest

from app.orchestrator.routing.config import load_config


def test_pipeline_budget_defaults_to_40(monkeypatch):
    monkeypatch.delenv("ORCH_PIPELINE_BUDGET_USD", raising=False)
    config = load_config(apply_env=True)
    assert config.pipeline_budget_usd == 40
    assert config.internal_budget_usd == 10


def test_pipeline_budget_env_overrides_default(monkeypatch):
    monkeypatch.setenv("ORCH_PIPELINE_BUDGET_USD", "12.5")
    config = load_config(apply_env=True)
    assert config.pipeline_budget_usd == 12.5
    assert config.internal_budget_usd == 10


@pytest.mark.parametrize("raw", ["nope", "", "nan", "NaN", "inf", "-inf", "-1"])
def test_pipeline_budget_env_rejects_invalid(monkeypatch, raw):
    monkeypatch.setenv("ORCH_PIPELINE_BUDGET_USD", raw)
    with pytest.raises(ValueError, match="ORCH_PIPELINE_BUDGET_USD"):
        load_config(apply_env=True)


@pytest.mark.parametrize("raw", ["0", "0.0", "-0", "-0.0", "true", "True"])
def test_pipeline_budget_rejects_zero_and_true(monkeypatch, raw):
    monkeypatch.setenv("ORCH_PIPELINE_BUDGET_USD", raw)
    with pytest.raises(ValueError, match="ORCH_PIPELINE_BUDGET_USD"):
        load_config(apply_env=True)


def test_pipeline_yaml_bool_and_missing_key(monkeypatch):
    from app.orchestrator.routing.config import _pipeline_budget_usd

    monkeypatch.delenv("ORCH_PIPELINE_BUDGET_USD", raising=False)
    with pytest.raises(ValueError, match="pipeline_sub_budget_usd"):
        _pipeline_budget_usd({"pipeline_sub_budget_usd": True}, apply_env=False)
    assert _pipeline_budget_usd({}, apply_env=False) == 40
