from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from app.orchestrator import auto_advance
from app.settings import settings


class _Sess:
    def __init__(self, name, row=None):
        self.name, self.row, self.commits, self.refreshed = name, row, 0, 0

    async def get(self, _model, _pid):
        return self.row

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        self.refreshed += 1


@pytest.fixture
def calls(monkeypatch):
    log: list[tuple] = []

    async def fake_advance(session, project, bot, *, force=False):
        log.append(("advance", session.name, project.name, force))
        return True

    monkeypatch.setattr(auto_advance, "maybe_auto_advance", fake_advance)
    return log


def _install_project_db(monkeypatch, calls, ps):
    import app.project_db as pdb

    @contextlib.asynccontextmanager
    async def scope(pid):
        calls.append(("scope", pid))
        yield ps

    async def pull(session, p):
        calls.append(("pull", session.name, p.name))

    async def push(session, p):
        calls.append(("push", session.name, p.name))

    monkeypatch.setattr(pdb, "project_db_session_scope", scope)
    monkeypatch.setattr(pdb, "pull_master_runtime_into_project", pull)
    monkeypatch.setattr(pdb, "push_runtime_to_master", push)


def test_flag_is_off_by_default_and_has_an_env_alias():
    field = type(settings).model_fields["worker_use_project_db"]
    assert field.default is False
    assert field.alias == "WORKER_USE_PROJECT_DB"


async def test_flag_off_runs_on_master_session(monkeypatch, calls):
    monkeypatch.setattr(settings, "worker_use_project_db", False)
    master = _Sess("master")
    project = SimpleNamespace(id=7, name="master-row")
    assert await auto_advance.maybe_auto_advance_for_worker(master, project, None) is True
    assert calls == [("advance", "master", "master-row", False)]


async def test_flag_on_runs_on_project_db_between_pull_and_push(monkeypatch, calls):
    monkeypatch.setattr(settings, "worker_use_project_db", True)
    row = SimpleNamespace(id=7, name="project-row")
    ps = _Sess("project", row)
    _install_project_db(monkeypatch, calls, ps)
    master = _Sess("master")
    project = SimpleNamespace(id=7, name="master-row")
    assert await auto_advance.maybe_auto_advance_for_worker(master, project, None, force=True) is True
    assert calls == [("scope", 7), ("pull", "project", "project-row"),
                     ("advance", "project", "project-row", True), ("push", "project", "project-row")]
    assert ps.commits == 1
    assert master.refreshed == 1


async def test_flag_on_without_project_row_falls_back_to_master(monkeypatch, calls):
    monkeypatch.setattr(settings, "worker_use_project_db", True)
    ps = _Sess("project", None)
    _install_project_db(monkeypatch, calls, ps)
    master = _Sess("master")
    project = SimpleNamespace(id=7, name="master-row")
    assert await auto_advance.maybe_auto_advance_for_worker(master, project, None) is True
    assert calls == [("scope", 7), ("advance", "master", "master-row", False)]
    assert ps.commits == 0


async def test_flag_on_error_in_tick_does_not_push_runtime(monkeypatch, calls):
    monkeypatch.setattr(settings, "worker_use_project_db", True)
    ps = _Sess("project", SimpleNamespace(id=7, name="project-row"))
    _install_project_db(monkeypatch, calls, ps)

    async def boom(session, project, bot, *, force=False):
        raise RuntimeError("tick failed")

    monkeypatch.setattr(auto_advance, "maybe_auto_advance", boom)
    with pytest.raises(RuntimeError):
        await auto_advance.maybe_auto_advance_for_worker(_Sess("master"), SimpleNamespace(id=7, name="m"), None)
    assert ("push", "project", "project-row") not in calls


def test_worker_loop_calls_the_flagged_entry_point():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert "maybe_auto_advance_for_worker(s, ap, bot)" in src
    assert "await maybe_auto_advance(s, ap, bot)" not in src
