"""W4-4 (C05): выключенная режиссура сцен пишет явное «пропущено», а не молча проходит. Без сети и без модели.

Файл назван ``*_test.py``, чтобы pytest его собирал при префиксе ``w4_4_``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import Project, ProjectStatus
from app.orchestrator.steps import scene_design as step


class _Session:
    def __init__(self):
        self.flushed = 0
        self.committed = 0

    async def flush(self):
        self.flushed += 1

    async def commit(self):
        self.committed += 1


@pytest.fixture
def facts(monkeypatch):
    seen = []

    async def record_fact(kind, payload=None, **kw):
        seen.append((kind, payload))

    monkeypatch.setattr("app.services.observation.record_fact", record_fact)
    progress = []

    async def progress_text(session, project, text):
        progress.append(text)

    monkeypatch.setattr("app.services.run_sync.update_active_node_progress_text", progress_text)
    return SimpleNamespace(events=seen, progress=progress)


def _project(status, meta=None) -> Project:
    return Project(slug="w44-sd", topic="t", status=status, meta=meta or {})


@pytest.fixture(autouse=True)
def _disabled(monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "scene_design_enabled", False)


@pytest.mark.asyncio
async def test_agents_phase_disabled_is_recorded_as_skipped(facts) -> None:
    project, session = _project(ProjectStatus.scene_designing), _Session()
    await step.run(session, project)
    assert project.status is ProjectStatus.scene_agents_ready  # переход прежний: пайплайн не ломается
    assert session.committed == 1
    ((kind, payload),) = facts.events
    assert kind == "scene_design_skipped"
    assert payload["phase"] == "agents" and payload["reason"] == "scene_design_disabled"
    assert payload["model_called"] is False and "Пропущено" in payload["summary_ru"]
    assert project.meta["scene_design_skipped"]["agents"]["reason"] == "scene_design_disabled"
    assert facts.progress and facts.progress[0].startswith("Пропущено: режиссура сцен выключена")


@pytest.mark.asyncio
async def test_assemble_phase_disabled_is_recorded_as_skipped(facts) -> None:
    project, session = _project(ProjectStatus.scene_assembling), _Session()
    await step.run_assemble(session, project)
    assert project.status is ProjectStatus.scene_design_ready
    ((kind, payload),) = facts.events
    assert kind == "scene_design_skipped" and payload["phase"] == "assemble"
    assert "сборка сцен" in payload["summary_ru"]


@pytest.mark.asyncio
async def test_both_phases_keep_their_own_marks_in_the_project(facts) -> None:
    project, session = _project(ProjectStatus.scene_designing), _Session()
    await step.run(session, project)
    project.status = ProjectStatus.scene_assembling
    await step.run_assemble(session, project)
    assert set(project.meta["scene_design_skipped"]) == {"agents", "assemble"}


@pytest.mark.asyncio
async def test_a_failing_journal_does_not_stop_the_skip(monkeypatch) -> None:
    async def broken(kind, payload=None, **kw):
        raise RuntimeError("journal down")

    async def broken_progress(session, project, text):
        raise RuntimeError("no run")

    monkeypatch.setattr("app.services.observation.record_fact", broken)
    monkeypatch.setattr("app.services.run_sync.update_active_node_progress_text", broken_progress)
    project, session = _project(ProjectStatus.scene_designing), _Session()
    await step.run(session, project)
    assert project.status is ProjectStatus.scene_agents_ready
    assert project.meta["scene_design_skipped"]["agents"]["model_called"] is False


@pytest.mark.asyncio
async def test_the_wrong_status_is_still_a_silent_return_without_a_skip_mark(facts) -> None:
    """Вызов не в свою фазу ничего не делал и не должен выдавать «пропущено»."""
    project, session = _project(ProjectStatus.frames_ready), _Session()
    await step.run(session, project)
    await step.run_assemble(session, project)
    assert facts.events == [] and "scene_design_skipped" not in project.meta
    assert project.status is ProjectStatus.frames_ready


@pytest.mark.asyncio
async def test_a_project_override_enables_the_step_so_nothing_is_skipped(facts, monkeypatch) -> None:
    """При включённой режиссуре фаза идёт по обычному пути (здесь он остановится на отсутствии кадров), без пометки."""
    from app.services.scene_design import runner

    project, session = _project(ProjectStatus.scene_assembling, {"scene_design_enabled": True}), _Session()
    assert runner.scene_design_enabled(project) is True

    async def no_frames(session, project):
        return []

    monkeypatch.setattr(step, "_load_frames", no_frames)
    with pytest.raises(RuntimeError, match="нет кадров"):
        await step.run_assemble(session, project)
    assert facts.events == [] and "scene_design_skipped" not in project.meta
