from __future__ import annotations

import os

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base, Frame, NodeRun, NodeRunStatus, Project, ProjectStatus, WorkflowRun,
)
from app.orchestrator import node_registry
from app.services import observation
from app.services.observation import (
    ExecutionControl, ObservationOutbox, Observer, auto_observe_enabled, observe_attempt,
    observe_only_result_check, record_project_event,
)

GOOD_PLAN = "Общий план ролика. " * 60
GOOD_SCRIPT = "Закадровый текст ролика. " * 30


@pytest.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSERVER_AUTO_ENABLE", "1")
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'master.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await observation.migrate_observation_schema(conn)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    observer = Observer(maker, body_root=tmp_path / "bodies")
    monkeypatch.setattr(observation, "_observer", observer)
    yield observer, maker
    await engine.dispose()


_SLUG = iter(range(10_000))


async def _project(maker, *, status=ProjectStatus.planning, nodes=None, meta=None, **fields):
    nodes = nodes if nodes is not None else [{"id": "n1", "type": "plan"}, {"id": "n2", "type": "script"}]
    async with maker() as s:
        project = Project(slug=f"auto-observe-{next(_SLUG)}", topic="тема", status=status, meta=meta or {}, **fields)
        s.add(project)
        await s.flush()
        run = WorkflowRun(workflow_id=1, project_id=project.id, nodes_snapshot=nodes, edges_snapshot=[])
        s.add(run)
        await s.flush()
        for n in nodes:
            s.add(NodeRun(workflow_run_id=run.id, node_key=n["id"], node_type=n["type"],
                          status=NodeRunStatus.running if n is nodes[0] else NodeRunStatus.pending))
        await s.commit()
        return project.id, run.id


async def _kinds(maker, project_id):
    async with maker() as s:
        rows = (await s.scalars(select(ObservationOutbox).where(
            ObservationOutbox.project_id == project_id).order_by(ObservationOutbox.id))).all()
        return [(r.kind, r.node_key, r.payload) for r in rows]



def test_default_is_on_outside_pytest_and_switches_work(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("OBSERVER_AUTO_ENABLE", raising=False)
    assert auto_observe_enabled(None) is True
    assert auto_observe_enabled(type("P", (), {"meta": {"observer_auto": False}})()) is False
    assert auto_observe_enabled(type("P", (), {"meta": {"observer_auto": True}})()) is True
    for off in ("0", "false", "off", "no"):
        monkeypatch.setenv("OBSERVER_AUTO_ENABLE", off)
        assert auto_observe_enabled(None) is False
    monkeypatch.setenv("OBSERVER_AUTO_ENABLE", "1")
    assert auto_observe_enabled(None) is True


def test_default_is_off_under_pytest_by_conftest_env():
    assert os.environ.get("OBSERVER_AUTO_ENABLE") == "0"
    assert auto_observe_enabled(None) is False



async def test_auto_scope_covers_every_node_without_explicit_enable(env):
    observer, maker = env
    pid, run_id = await _project(maker)
    async with maker() as s:
        project = await s.get(Project, pid)
    assert observer.scopes == {}
    scope = await observer.ensure_auto_scope(project)
    assert scope is not None and scope.mode == "observe" and scope.observer_state == "active"
    assert scope.node_keys == ["n1", "n2"] and scope.workflow_run_id == run_id
    assert observer.scopes[run_id] is scope
    assert await observer.ensure_auto_scope(project) is scope
    async with maker() as s:
        assert len((await s.scalars(select(ExecutionControl))).all()) == 1
    control = [p for k, n, p in await _kinds(maker, pid) if k == "observer_control"]
    assert control and control[0]["initiator"] == "auto_observe"


async def test_stopped_scope_is_not_re_enabled_and_off_switches_hold(env, monkeypatch):
    observer, maker = env
    pid, _ = await _project(maker)
    async with maker() as s:
        project = await s.get(Project, pid)
    scope = await observer.ensure_auto_scope(project)
    await observer.stop(scope.execution_id, initiator="operator")
    observer.scopes.clear()
    assert await observer.ensure_auto_scope(project) is None
    pid2, _ = await _project(maker, meta={"observer_auto": False})
    async with maker() as s:
        off_project = await s.get(Project, pid2)
    assert await observer.ensure_auto_scope(off_project) is None
    monkeypatch.setenv("OBSERVER_AUTO_ENABLE", "0")
    pid3, _ = await _project(maker)
    async with maker() as s:
        p3 = await s.get(Project, pid3)
    assert await observer.ensure_auto_scope(p3) is None


async def test_project_without_run_and_broken_master_never_raise(env):
    observer, maker = env
    async with maker() as s:
        project = Project(slug="no-run", topic="t", status=ProjectStatus.planning, meta={})
        s.add(project)
        await s.commit()
    assert await observer.ensure_auto_scope(project) is None

    def broken():
        raise RuntimeError("master down")
    observer.master = broken
    assert await observer.ensure_auto_scope(project) is None
    assert observer.auto_failures[project.id] == "RuntimeError"


async def test_explicit_scope_is_kept_and_not_duplicated(env):
    observer, maker = env
    pid, run_id = await _project(maker)
    await observer.enable(project_id=pid, workflow_run_id=run_id, node_keys=["n1"], graph_version="g")
    async with maker() as s:
        project = await s.get(Project, pid)
    scope = await observer.ensure_auto_scope(project)
    assert scope.node_keys == ["n1"]
    async with maker() as s:
        assert len((await s.scalars(select(ExecutionControl))).all()) == 1



RUNNING_TYPES = sorted((spec.running_status.value, typ) for typ, spec in node_registry.WORK_NODES.items()
                       if not typ.startswith("enrich_"))


@pytest.mark.parametrize("status_value,node_type", RUNNING_TYPES)
async def test_every_node_type_gets_start_and_end_without_manual_enable(env, status_value, node_type):
    observer, maker = env
    status = ProjectStatus(status_value)
    pid, _ = await _project(maker, status=status, nodes=[{"id": "n1", "type": node_type}])
    async with maker() as s:
        project = await s.get(Project, pid)
        async with observe_attempt(s, project):
            pass
        await s.commit()
    kinds = [k for k, n, p in await _kinds(maker, pid)]
    assert "node_start" in kinds and "node_end" in kinds, (node_type, kinds)
    started = next((n for k, n, p in await _kinds(maker, pid) if k == "node_start"))
    assert started == "n1"


async def test_excel_gpt_node_gets_start_and_end(env):
    observer, maker = env
    pid, _ = await _project(maker, status=ProjectStatus.enriching_1,
                            nodes=[{"id": "ex1", "type": "excel_gpt"}], meta={"active_excel_gpt_node_key": "ex1"})
    async with maker() as s:
        project = await s.get(Project, pid)
        async with observe_attempt(s, project):
            pass
        await s.commit()
    kinds = [k for k, n, p in await _kinds(maker, pid)]
    assert "node_start" in kinds and "node_end" in kinds


async def test_switch_off_gives_no_events_and_native_work_still_runs(env, monkeypatch):
    observer, maker = env
    monkeypatch.setenv("OBSERVER_AUTO_ENABLE", "0")
    pid, _ = await _project(maker)
    ran = []
    async with maker() as s:
        project = await s.get(Project, pid)
        async with observe_attempt(s, project):
            ran.append(1)
        await s.commit()
    assert ran == [1] and await _kinds(maker, pid) == []



async def _run_plan(maker, text):
    pid, _ = await _project(maker, general_plan=text)
    async with maker() as s:
        project = await s.get(Project, pid)
        async with observe_attempt(s, project):
            pass
        await s.commit()
    return pid


async def test_broken_plan_in_observe_mode_gives_incident_and_does_not_block(env):
    observer, maker = env
    pid = await _run_plan(maker, "коротко")
    events = await _kinds(maker, pid)
    verification = [p for k, n, p in events if k == "node_result_verification"]
    assert verification and verification[0]["status"] == "failed" and verification[0]["observe_only"] is True
    assert verification[0]["run_held"] is False
    checks = [p for k, n, p in events if k == "runtime_check" and p.get("check") == "observe_only_plan_result"]
    assert checks and checks[0]["status"] == "failed" and checks[0]["performed"] is True
    assert "node_end" in [k for k, n, p in events]
    assert "node_error" not in [k for k, n, p in events]


async def test_good_plan_passes_without_incident_event(env):
    observer, maker = env
    pid = await _run_plan(maker, GOOD_PLAN)
    events = await _kinds(maker, pid)
    verification = [p for k, n, p in events if k == "node_result_verification"]
    assert verification and verification[0]["status"] == "passed" and verification[0]["observe_only"] is True
    assert not [1 for k, n, p in events if k == "runtime_check" and p.get("status") == "failed"]


async def test_incident_is_derived_from_observe_only_failure(env):
    from app.services.diagnosis import Diagnoser
    observer, maker = env
    pid = await _run_plan(maker, "коротко")
    events = await _kinds(maker, pid)
    failed = [p for k, n, p in events if k == "runtime_check" and p.get("performed") is True
              and p.get("status") in {"failed", "rejected"}]
    assert failed and failed[0].get("failure_class") not in {"invalid_json", "missing_payload"}


async def test_split_and_image_prompts_checks_observe_only(env):
    observer, maker = env
    nodes = [{"id": "n1", "type": "split"}]
    pid, _ = await _project(maker, status=ProjectStatus.splitting, nodes=nodes)
    async with maker() as s:
        s.add(Frame(project_id=pid, number=1, uuid="a" * 32, voiceover_text="Один."))
        s.add(Frame(project_id=pid, number=3, uuid="b" * 32, voiceover_text=""))
        await s.commit()
    async with maker() as s:
        project = await s.get(Project, pid)
        async with observe_attempt(s, project):
            pass
        await s.commit()
    events = await _kinds(maker, pid)
    proof = [p for k, n, p in events if k == "node_result_verification"][0]
    assert proof["node_type"] == "split" and proof["status"] == "failed" and proof["observe_only"] is True
    assert "frame_order" in proof["errors"] and "empty_voiceover" in proof["errors"]
    assert [p for k, n, p in events if k == "runtime_check" and p["check"] == "observe_only_split_result"]

    pid2, _ = await _project(maker, status=ProjectStatus.generating_image_prompts,
                             nodes=[{"id": "n1", "type": "image_prompts"}])
    async with maker() as s:
        s.add(Frame(project_id=pid2, number=1, uuid="c" * 32, voiceover_text="Текст."))
        await s.commit()
    async with maker() as s:
        project = await s.get(Project, pid2)
        async with observe_attempt(s, project):
            pass
        await s.commit()
    proof2 = [p for k, n, p in await _kinds(maker, pid2) if k == "node_result_verification"][0]
    assert proof2["node_type"] == "image_prompts" and proof2["status"] == "unknown" and proof2["observe_only"] is True
    assert proof2["run_held"] is False


async def test_check_never_raises_and_skips_managed_and_other_types(env):
    observer, maker = env
    pid, _ = await _project(maker)
    async with maker() as s:
        project = await s.get(Project, pid)
    scope = await observer.ensure_auto_scope(project)
    ctx = observation.AttemptContext(observer, scope, None, "master", "n1", "attempt")
    assert await observe_only_result_check(ctx, None, project, "split") is None
    assert await observe_only_result_check(ctx, None, project, "hero") is None
    scope.mode = "managed"
    assert await observe_only_result_check(ctx, None, project, "plan") is None


async def test_native_failure_still_propagates_with_auto_observation(env):
    observer, maker = env
    pid, _ = await _project(maker, general_plan="x")
    async with maker() as s:
        project = await s.get(Project, pid)
        with pytest.raises(ValueError):
            async with observe_attempt(s, project):
                raise ValueError("native failure")
        await s.commit()
    assert "node_error" in [k for k, n, p in await _kinds(maker, pid)]



async def test_body_size_limit_is_a_visible_gap_not_a_silent_drop(env):
    observer, maker = env
    pid, _ = await _project(maker)
    async with maker() as s:
        project = await s.get(Project, pid)
    scope = await observer.ensure_auto_scope(project)
    saved = observer.save_body(scope.execution_id, {"x": "я" * (Observer.MAX_BODY + 10)})
    assert saved["gap"] == "body_size_limit" and saved["bytes"] > Observer.MAX_BODY



async def test_record_project_event_stages_in_callers_transaction(env):
    observer, maker = env
    pid, _ = await _project(maker)
    async with maker() as s:
        project = await s.get(Project, pid)
        assert await record_project_event(s, project, "runtime_check", {"check": "x", "performed": False}) is True
        await s.rollback()
    assert [k for k, n, p in await _kinds(maker, pid) if k == "runtime_check"] == []
    async with maker() as s:
        project = await s.get(Project, pid)
        assert await record_project_event(s, project, "runtime_check", {"check": "x", "performed": False}) is True
        await s.commit()
    rows = [(k, n, p) for k, n, p in await _kinds(maker, pid) if k == "runtime_check"]
    assert len(rows) == 1 and rows[0][1] is None


async def test_record_project_event_returns_false_when_off(env, monkeypatch):
    observer, maker = env
    monkeypatch.setenv("OBSERVER_AUTO_ENABLE", "0")
    pid, _ = await _project(maker)
    async with maker() as s:
        project = await s.get(Project, pid)
        assert await record_project_event(s, project, "runtime_check", {}) is False
