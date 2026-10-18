"""waiting_approval: парковка через машину состояний и выход после одобрения."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    NodeRun,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.services.node_status_machine import transition_node_status
from app.services.project_steps import start_step
from app.services.run_sync import _aggregate_workflow_run_status
from app.services.step_failure_policy import _park_budget_exhausted
from app.settings import settings


@pytest.fixture
async def mem(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            yield session

    monkeypatch.setattr("app.db.session_scope", _scope)
    monkeypatch.setattr("app.services.run_sync.session_scope", _scope)
    yield _scope
    await engine.dispose()


async def _seed(scope, *, statuses: list[tuple[str, NodeRunStatus]]):
    async with scope() as session:
        wf = Workflow(name=f"wf-{uuid.uuid4().hex[:6]}", is_default=True, nodes=[], edges=[])
        session.add(wf)
        await session.flush()
        project = Project(
            slug=f"park-{uuid.uuid4().hex[:6]}",
            topic="t",
            status=ProjectStatus.generating_images,
            meta={},
        )
        session.add(project)
        await session.flush()
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.running,
            nodes_snapshot=[{"id": key, "type": "images", "data": {}} for key, _st in statuses],
            edges_snapshot=[],
        )
        session.add(run)
        await session.flush()
        ids = []
        for key, status in statuses:
            nr = NodeRun(
                workflow_run_id=run.id,
                node_key=key,
                node_type="images",
                status=status,
            )
            session.add(nr)
            await session.flush()
            ids.append(nr.id)
        await session.commit()
        return project.id, ids, wf.id


async def test_budget_park_goes_through_state_machine(mem, monkeypatch) -> None:
    monkeypatch.setattr(settings, "node_status_strict", True)
    project_id, ids, _wf = await _seed(
        mem,
        statuses=[
            ("n_run", NodeRunStatus.running),
            ("n_q", NodeRunStatus.queued),
        ],
    )
    calls: list[str] = []
    real = transition_node_status

    def _spy(nr, new_status, *, initiator, project_id=None, error=None):
        calls.append(initiator)
        return real(
            nr,
            new_status,
            initiator=initiator,
            project_id=project_id,
            error=error,
        )

    monkeypatch.setattr("app.services.node_status_machine.transition_node_status", _spy)
    async with mem() as session:
        project = await session.get(Project, project_id)
        action = await _park_budget_exhausted(session, project, RuntimeError("cap"))
        await session.commit()
        running = await session.get(NodeRun, ids[0])
        queued = await session.get(NodeRun, ids[1])
    assert action == "waiting_approval"
    assert calls == ["spend_guard"]
    assert running.status is NodeRunStatus.waiting_approval
    assert queued.status is NodeRunStatus.queued

    refused: list[str] = []

    def _refuse(nr, new_status, *, initiator, project_id=None, error=None):
        refused.append(initiator)
        return False

    monkeypatch.setattr("app.services.node_status_machine.transition_node_status", _refuse)
    project_id2, ids2, _wf2 = await _seed(
        mem, statuses=[("n_run2", NodeRunStatus.running)]
    )
    async with mem() as session:
        project = await session.get(Project, project_id2)
        was = project.status
        action2 = await _park_budget_exhausted(session, project, RuntimeError("cap"))
        node = await session.get(NodeRun, ids2[0])
        assert action2 != "waiting_approval"
        assert project.status is was
        assert node.status is NodeRunStatus.running
        assert (node.meta or {}).get("status") != "waiting_approval"
    assert refused == ["spend_guard"]


async def test_waiting_approval_resumes_after_approval(mem, monkeypatch) -> None:
    project_id, ids, wf_id = await _seed(
        mem, statuses=[("n_plan", NodeRunStatus.waiting_approval)]
    )

    async def _wf(_session=None):
        return wf_id

    async def _queue_ok(_session, _project):
        return None

    monkeypatch.setattr("app.services.run_sync._get_default_workflow_id", _wf)
    monkeypatch.setattr("app.services.gen_queue.assert_can_start_in_queue", _queue_ok)
    async with mem() as session:
        project = await session.get(Project, project_id)
        project.status = ProjectStatus.plan_ready
        project.meta = {"approval_status": "waiting_approval", "approval_reason": "budget_exhausted"}
        node = await session.get(NodeRun, ids[0])
        node.node_type = "plan"
        node.node_key = "n_plan"
        run = await session.get(WorkflowRun, node.workflow_run_id)
        run.nodes_snapshot = [{"id": "n_plan", "type": "plan", "data": {}}]
        await session.commit()
        await start_step(session, project, "plan")
        await session.commit()
        parked = await session.get(NodeRun, ids[0])
        assert parked.status is NodeRunStatus.waiting_approval

        project.meta = {
            **(project.meta or {}),
            "approval_status": "approved",
        }
        await session.commit()
        await start_step(session, project, "plan")
        await session.commit()
        fresh = await session.get(NodeRun, ids[0])
        assert fresh.status is NodeRunStatus.running


def test_aggregate_waiting_approval_is_not_done() -> None:
    run = SimpleNamespace(
        node_runs=[
            SimpleNamespace(status=NodeRunStatus.done),
            SimpleNamespace(status=NodeRunStatus.waiting_approval),
        ],
        status=WorkflowRunStatus.running,
        started_at=None,
        finished_at=None,
    )
    project = SimpleNamespace(status=ProjectStatus.planning)
    _aggregate_workflow_run_status(run, project)
    assert run.status is not WorkflowRunStatus.done
    assert run.finished_at is None


def test_managed_launch_refuses_over_parked_node() -> None:
    from app.services.managed_launch import validate_scope

    run = SimpleNamespace(
        nodes_snapshot=[{"id": "n_plan", "type": "plan", "data": {}}],
        edges_snapshot=[],
    )
    project = SimpleNamespace(meta={}, status=ProjectStatus.new)
    parked = SimpleNamespace(
        node_key="n_plan",
        status=NodeRunStatus.waiting_approval,
        attempts=0,
    )
    with pytest.raises(ValueError, match="active node"):
        validate_scope(run, project, [parked], ["n_plan"])
