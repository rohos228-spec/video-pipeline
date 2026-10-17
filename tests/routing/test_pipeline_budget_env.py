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
