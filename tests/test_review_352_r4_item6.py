"""Ревью #352 r4 п.6: нет маршрута 52pay не оставляет разбор стиля в running.

Регрессия фикса п.9: _publish(running) шёл до raise, а raise стоял до try.
"""

from contextlib import asynccontextmanager

import pytest

from app.services.style_agent import job as style_job


@pytest.mark.asyncio
async def test_missing_52pay_route_does_not_stay_running(monkeypatch, tmp_path) -> None:
    class _Project:
        id = 352
        meta: dict = {}
        data_dir = tmp_path

    project = _Project()
    published: list[str] = []

    class _Session:
        async def get(self, _model, _pid):
            return project

    @asynccontextmanager
    async def _scope(_pid):
        yield _Session()

    async def _publish(_pid, status, extra=None):
        published.append(status)

    monkeypatch.setattr("app.project_db.project_db_session_scope", _scope)
    monkeypatch.setattr(style_job, "_publish", _publish)
    monkeypatch.setattr(style_job, "resolve_model", lambda *_a, **_k: {"id": "gpt-5.6-sol"})
    monkeypatch.setattr(style_job, "list_ref_videos", lambda *_a, **_k: [])
    monkeypatch.setattr(style_job, "style_dir", lambda _project: tmp_path)
    monkeypatch.setattr(style_job, "_pipeline_text_override", lambda _model: None)

    await style_job._run_job(352, node_key="style", overrides={})

    job = project.meta["style_agent"]["job"]
    assert job["status"] != "running"
    assert job["status"] == "failed"
    assert "нет маршрута 52pay" in str(job.get("error") or "")
    assert published[-1] != "running"
    assert "failed" in published
