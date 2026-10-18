"""Неблокирующее: style_agent без маршрута 52pay падает на vibecode видимо."""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager

import pytest
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
    assert "нет маршрута 52pay для разбора стиля" in src
    assert "_pipeline_text_override" in src
    assert "_resolve_style_text_override" not in src


@pytest.mark.asyncio
async def test_fallback_line_is_on_the_job_and_status_is_not_running(
    monkeypatch, tmp_path
) -> None:
    from app.services.style_agent import job as style_job

    class _Project:
        id = 13
        meta: dict = {}
        data_dir = tmp_path

    project = _Project()

    class _Session:
        async def get(self, _model, _pid):
            return project

    @asynccontextmanager
    async def _scope(_pid):
        yield _Session()

    async def _extract(*_a, **_k):
        return {"passport": {"errors": {}, "style_id": "noir"}}

    async def _publish(*_a, **_k):
        return None

    monkeypatch.setattr("app.project_db.project_db_session_scope", _scope)
    monkeypatch.setattr("app.services.pay52_routes.resolve_text", lambda *_a, **_k: None)
    monkeypatch.setattr(style_job, "run_style_extract", _extract)
    monkeypatch.setattr(style_job, "_publish", _publish)
    monkeypatch.setattr(style_job, "style_dir", lambda _project: tmp_path)
    monkeypatch.setattr(style_job, "list_ref_videos", lambda *_a, **_k: [tmp_path / "a.mp4"])
    monkeypatch.setattr(
        style_job,
        "resolve_model",
        lambda *_a, **_k: {"id": "gpt-5.6-sol", "kind": "text", "label": "sol"},
    )

    await style_job._run_job(13, node_key="style_extract", overrides={})
    job = project.meta["style_agent"]["job"]
    assert job["status"] != "running"
    assert job["status"] == "failed"
    assert "нет маршрута 52pay" in str(job.get("error") or "")
    assert job["status"] != "done"


@pytest.mark.asyncio
async def test_fallback_error_does_not_leave_job_running(monkeypatch, tmp_path) -> None:
    from app.services.style_agent import job as style_job

    class _Project:
        id = 13
        meta: dict = {}
        data_dir = tmp_path

    project = _Project()

    class _Session:
        async def get(self, _model, _pid):
            return project

    @asynccontextmanager
    async def _scope(_pid):
        yield _Session()

    async def _boom(*_a, **_k):
        raise RuntimeError("extract упал")

    async def _publish(*_a, **_k):
        return None

    class _Route:
        api_model = "gpt-5.6-sol"

    monkeypatch.setattr("app.project_db.project_db_session_scope", _scope)
    monkeypatch.setattr("app.services.pay52_routes.resolve_text", lambda *_a, **_k: _Route())
    monkeypatch.setattr(style_job, "run_style_extract", _boom)
    monkeypatch.setattr(style_job, "_publish", _publish)
    monkeypatch.setattr(style_job, "style_dir", lambda _project: tmp_path)
    monkeypatch.setattr(style_job, "list_ref_videos", lambda *_a, **_k: [tmp_path / "a.mp4"])
    monkeypatch.setattr(
        style_job,
        "resolve_model",
        lambda *_a, **_k: {"id": "gpt-5.6-sol", "kind": "text", "label": "sol"},
    )

    await style_job._run_job(13, node_key="style_extract", overrides={})
    job = project.meta["style_agent"]["job"]
    assert job["status"] != "running"
    assert job["status"] == "failed"
    assert "extract упал" in str(job.get("error") or "")
