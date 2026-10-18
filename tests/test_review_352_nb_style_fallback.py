"""Неблокирующее: style_agent без маршрута 52pay завершается failed."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest


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

    published: list[str] = []

    async def _extract(*_a, **_k):
        return {"passport": {"errors": {}, "style_id": "noir"}}

    async def _publish(*_a, **_k):
        published.append(str(_a[1] if len(_a) > 1 else _k.get("status")))
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
    assert "failed" in published


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
