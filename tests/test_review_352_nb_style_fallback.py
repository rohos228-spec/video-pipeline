"""Неблокирующее: style_agent без маршрута 52pay падает на vibecode видимо."""

from __future__ import annotations

import inspect

from loguru import logger

from app.services.model_substitution import (
    clear_substitution_events,
    style_job_substitution_patch,
    substitution_events,
)
from app.services.style_agent.job import _resolve_style_text_override, _run_job


def test_style_agent_vibecode_fallback_is_visible(monkeypatch) -> None:
    calls: list[dict] = []

    def _no_route(_model: str | None):
        return None

    def _track(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("app.services.pay52_routes.resolve_text", _no_route)
    monkeypatch.setattr("app.services.api_tracker_hook.record_api_call", _track)
    clear_substitution_events()
    warnings: list[str] = []
    sink = logger.add(lambda msg: warnings.append(str(msg)), level="WARNING")
    try:
        ov, line = _resolve_style_text_override(
            {"id": "gpt-5.6-sol", "kind": "text", "label": "sol"},
            project_id=13,
            node_key="style_extract",
        )
    finally:
        logger.remove(sink)

    assert ov is not None
    assert ov.provider == "vibecode"
    assert line
    assert "52pay:gpt-5.6-sol" in line
    assert "vibecode:gpt-5.6-sol" in line
    assert any(
        "52pay:gpt-5.6-sol" in m and "vibecode:gpt-5.6-sol" in m and "52pay" in m
        for m in warnings
    )
    meta = calls[0]["metadata"]
    assert meta["original_model"] == "52pay:gpt-5.6-sol"
    assert meta["substituted_model"] == "vibecode:gpt-5.6-sol"
    assert meta["reason"]
    assert meta["node"] == "style_extract"
    assert meta["run"] == "13"
    assert calls[0]["call_type"] == "model_substitution"
    events = substitution_events()
    assert events[-1]["line"] == line
    patch = style_job_substitution_patch(line)
    assert patch["job"]["model_substitution"] == line
    src = inspect.getsource(_run_job)
    assert "нет маршрута 52pay" in src
    assert "style_job_substitution_patch" not in src
