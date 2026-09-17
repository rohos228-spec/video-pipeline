"""Master state.db vs project.db: ▶ не должен крутить ноду вхолостую."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.models import (
    Base,
    NodeRun,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
)
from app.project_db import (
    _copy_noderuns_between_sessions,
    close_all_project_engines,
    get_project_sessionmaker,
    init_project_db,
    pull_master_runtime_into_project,
    push_runtime_to_master,
    push_runtime_to_project_db,
)
from app.services.project_control import pause_project
from app.services.project_steps import start_step
from app.services.step_failure_policy import record_step_failure


def _fake_master_session() -> SimpleNamespace:
    return SimpleNamespace(
        get_bind=lambda: SimpleNamespace(url="sqlite+aiosqlite:///data/state.db")
    )


@pytest.mark.asyncio
async def test_push_runtime_copies_running_status_into_project_db() -> None:
    p = Project(
        id=777,
        slug="sync-runtime",
        title="sync",
        topic="t",
        status=ProjectStatus.script_ready,
        meta={"keep_local": True},
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    await init_project_db(p.data_dir, project=p)

    p.status = ProjectStatus.enriching_1
    p.meta = {"active_excel_gpt_node_key": "n_excel_gpt_fw_report"}
    await push_runtime_to_project_db(_fake_master_session(), p)

    sm = await get_project_sessionmaker(p.data_dir)
    async with sm() as session:
        row = await session.get(Project, 777)
        assert row is not None
        assert row.status is ProjectStatus.enriching_1
        assert (row.meta or {}).get("keep_local") is True
        assert (row.meta or {}).get("active_excel_gpt_node_key") == "n_excel_gpt_fw_report"

    await close_all_project_engines()


@pytest.mark.asyncio
async def test_pull_master_runtime_overwrites_stale_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with master_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    master_factory = async_sessionmaker(master_engine, expire_on_commit=False)

    async with master_factory() as ms:
        ms.add(
            Project(
                id=778,
                slug="pull-runtime",
                title="pull",
                topic="t",
                status=ProjectStatus.enriching_1,
                meta={"active_excel_gpt_node_key": "n_excel_gpt_fw_report"},
            )
        )
        await ms.commit()

    @asynccontextmanager
    async def master_scope():
        async with master_factory() as s:
            yield s

    monkeypatch.setattr("app.db.session_scope", master_scope)

    p = Project(
        id=778,
        slug="pull-runtime",
        title="pull",
        topic="t",
        status=ProjectStatus.enrich_1_ready,
        meta={"user_stop": True, "keep_local": True},
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    await init_project_db(p.data_dir, project=p)

    sm = await get_project_sessionmaker(p.data_dir)
    async with sm() as session:
        row = await session.get(Project, 778)
        assert row is not None
        changed = await pull_master_runtime_into_project(session, row)
        assert changed is True
        assert row.status is ProjectStatus.enriching_1
        assert (row.meta or {}).get("active_excel_gpt_node_key") == "n_excel_gpt_fw_report"
        assert "user_stop" not in (row.meta or {})
        assert (row.meta or {}).get("keep_local") is True
        await session.commit()

    await master_engine.dispose()
    await close_all_project_engines()


@pytest.mark.asyncio
async def test_pull_master_runtime_skips_stale_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with master_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    master_factory = async_sessionmaker(master_engine, expire_on_commit=False)

    async with master_factory() as ms:
        ms.add(
            Project(
                id=780,
                slug="pull-skip-paused",
                title="pull",
                topic="t",
                status=ProjectStatus.paused,
            )
        )
        await ms.commit()

    @asynccontextmanager
    async def master_scope():
        async with master_factory() as s:
            yield s

    monkeypatch.setattr("app.db.session_scope", master_scope)

    p = Project(
        id=780,
        slug="pull-skip-paused",
        title="pull",
        topic="t",
        status=ProjectStatus.image_prompts_ready,
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    await init_project_db(p.data_dir, project=p)

    sm = await get_project_sessionmaker(p.data_dir)
    async with sm() as session:
        row = await session.get(Project, 780)
        assert row is not None
        await pull_master_runtime_into_project(session, row)
        assert row.status is ProjectStatus.image_prompts_ready
        await session.commit()

    await master_engine.dispose()
    await close_all_project_engines()


@pytest.mark.asyncio
async def test_pull_master_runtime_takes_parked_sleep_pause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Master paused после 30-мин сна — канвас должен взять паузу, не крутить ноду."""
    master_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with master_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    master_factory = async_sessionmaker(master_engine, expire_on_commit=False)

    async with master_factory() as ms:
        ms.add(
            Project(
                id=781,
                slug="pull-sleep-paused",
                title="pull",
                topic="t",
                status=ProjectStatus.paused,
                meta={
                    "step_failure": {
                        "sleep_until": "2099-01-01T00:00:00+00:00",
                        "last_running": "enriching_1",
                        "total_fails": {"enriching_1": 3},
                    },
                    "active_excel_gpt_node_key": "n_excel_gpt_fw_script",
                },
            )
        )
        await ms.commit()

    @asynccontextmanager
    async def master_scope():
        async with master_factory() as s:
            yield s

    monkeypatch.setattr("app.db.session_scope", master_scope)

    p = Project(
        id=781,
        slug="pull-sleep-paused",
        title="pull",
        topic="t",
        status=ProjectStatus.enriching_1,
        meta={"active_excel_gpt_node_key": "n_excel_gpt_fw_script"},
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    await init_project_db(p.data_dir, project=p)

    sm = await get_project_sessionmaker(p.data_dir)
    async with sm() as session:
        row = await session.get(Project, 781)
        assert row is not None
        row.status = ProjectStatus.enriching_1
        changed = await pull_master_runtime_into_project(session, row)
        assert changed is True
        assert row.status is ProjectStatus.paused
        await session.commit()

    await master_engine.dispose()
    await close_all_project_engines()


@pytest.mark.asyncio
async def test_record_step_failure_sleep_parks_project_db_noderun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сон 30 мин на state.db не должен оставлять fw-ноду running в project.db."""
    master_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with master_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    master_factory = async_sessionmaker(master_engine, expire_on_commit=False)

    async with master_factory() as ms:
        ms.add(
            Project(
                id=782,
                slug="sleep-park-noderun",
                title="sleep",
                topic="t",
                status=ProjectStatus.enriching_1,
                meta={
                    "active_excel_gpt_node_key": "n_excel_gpt_fw_script",
                    "step_failure": {"total_fails": {"enriching_1": 2}},
                },
            )
        )
        await ms.commit()

    @asynccontextmanager
    async def master_scope():
        async with master_factory() as s:
            yield s

    monkeypatch.setattr("app.db.session_scope", master_scope)

    p = Project(
        id=782,
        slug="sleep-park-noderun",
        title="sleep",
        topic="t",
        status=ProjectStatus.enriching_1,
        meta={"active_excel_gpt_node_key": "n_excel_gpt_fw_script"},
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    await init_project_db(p.data_dir, project=p)

    sm = await get_project_sessionmaker(p.data_dir)
    async with sm() as session:
        row = await session.get(Project, 782)
        assert row is not None
        row.status = ProjectStatus.enriching_1
        session.add(Workflow(id=1, name="default", is_default=True))
        await session.flush()
        run = WorkflowRun(
            id=1,
            workflow_id=1,
            project_id=782,
            nodes_snapshot=[{"id": "n_excel_gpt_fw_script", "type": "excel_gpt"}],
        )
        session.add(run)
        await session.flush()
        session.add(
            NodeRun(
                workflow_run_id=run.id,
                node_key="n_excel_gpt_fw_script",
                node_type="excel_gpt",
                status=NodeRunStatus.running,
                progress=15,
                progress_text="GPT",
            )
        )
        await session.commit()

    async with master_factory() as ms:
        m = await ms.get(Project, 782)
        assert m is not None
        action = await record_step_failure(
            ms,
            m,
            error=RuntimeError("upstream error"),
        )
        assert action == "sleep"
        assert m.status is ProjectStatus.paused
        await ms.commit()

    async with sm() as session:
        row = await session.get(Project, 782)
        assert row is not None
        assert row.status is ProjectStatus.paused
        run = (
            await session.execute(
                select(WorkflowRun)
                .where(WorkflowRun.project_id == 782)
                .options(selectinload(WorkflowRun.node_runs))
            )
        ).scalar_one()
        keys = {nr.node_key: nr for nr in run.node_runs}
        assert keys["n_excel_gpt_fw_script"].status is not NodeRunStatus.running

    await master_engine.dispose()
    await close_all_project_engines()


@pytest.mark.asyncio
async def test_pause_project_stops_running_noderun() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        p = Project(
            slug="pause-noderun",
            title="pause",
            topic="t",
            status=ProjectStatus.enriching_1,
            meta={"active_excel_gpt_node_key": "n_excel_gpt_fw_script"},
        )
        session.add(p)
        await session.flush()
        session.add(Workflow(id=1, name="default", is_default=True))
        await session.flush()
        run = WorkflowRun(
            workflow_id=1,
            project_id=p.id,
            nodes_snapshot=[{"id": "n_excel_gpt_fw_script", "type": "excel_gpt"}],
        )
        session.add(run)
        await session.flush()
        session.add(
            NodeRun(
                workflow_run_id=run.id,
                node_key="n_excel_gpt_fw_script",
                node_type="excel_gpt",
                status=NodeRunStatus.running,
            )
        )
        await session.flush()

        await pause_project(session, p)
        await session.commit()

        assert p.status is ProjectStatus.paused
        run = (
            await session.execute(
                select(WorkflowRun)
                .where(WorkflowRun.project_id == p.id)
                .options(selectinload(WorkflowRun.node_runs))
            )
        ).scalar_one()
        assert run.node_runs[0].status is not NodeRunStatus.running

    await engine.dispose()


@pytest.mark.asyncio
async def test_start_step_pushes_running_status_to_project_db() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        p = Project(
            slug="start-push",
            title="start",
            topic="t",
            status=ProjectStatus.script_ready,
            general_plan="x" * 200,
            script_text="script",
            meta={"keep_local": True},
        )
        session.add(p)
        await session.flush()
        p.data_dir.mkdir(parents=True, exist_ok=True)
        await init_project_db(p.data_dir, project=p)

        status = await start_step(
            session, p, "split", skip_queue_guard=True, explicit_ui_start=True
        )
        assert status is ProjectStatus.splitting

        sm = await get_project_sessionmaker(p.data_dir)
        async with sm() as pdb:
            row = await pdb.get(Project, p.id)
            assert row is not None
            assert row.status is ProjectStatus.splitting
            assert (row.meta or {}).get("keep_local") is True

    await engine.dispose()
    await close_all_project_engines()


@pytest.mark.asyncio
async def test_push_runtime_to_master_copies_running_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with master_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    master_factory = async_sessionmaker(master_engine, expire_on_commit=False)

    async with master_factory() as ms:
        ms.add(
            Project(
                id=779,
                slug="push-master",
                title="push",
                topic="t",
                status=ProjectStatus.enrich_1_ready,
                meta={"keep_local": True},
            )
        )
        await ms.commit()

    @asynccontextmanager
    async def master_scope():
        async with master_factory() as s:
            yield s

    monkeypatch.setattr("app.db.session_scope", master_scope)

    p = Project(
        id=779,
        slug="push-master",
        title="push",
        topic="t",
        status=ProjectStatus.enriching_1,
        meta={
            "active_excel_gpt_node_key": "n_excel_gpt_fw_report",
            "keep_local": True,
        },
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    await init_project_db(p.data_dir, project=p)

    sm = await get_project_sessionmaker(p.data_dir)
    async with sm() as session:
        row = await session.get(Project, 779)
        assert row is not None
        row.status = ProjectStatus.enriching_1
        row.meta = {
            **(row.meta or {}),
            "active_excel_gpt_node_key": "n_excel_gpt_fw_report",
        }
        await push_runtime_to_master(session, row)

    async with master_factory() as ms:
        m = await ms.get(Project, 779)
        assert m is not None
        assert m.status is ProjectStatus.enriching_1
        assert (m.meta or {}).get("active_excel_gpt_node_key") == "n_excel_gpt_fw_report"
        assert (m.meta or {}).get("keep_local") is True

    await master_engine.dispose()
    await close_all_project_engines()


@pytest.mark.asyncio
async def test_copy_noderuns_creates_missing_group_nodes() -> None:
    """state.db без fw_* не должен глотать running-ноду группы из project.db."""
    src_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    dest_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with src_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with dest_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    src_factory = async_sessionmaker(src_engine, expire_on_commit=False)
    dest_factory = async_sessionmaker(dest_engine, expire_on_commit=False)

    async def _seed(factory, *, with_group: bool) -> None:
        async with factory() as s:
            s.add(Project(id=34, slug="saltykova-1609", title="s", topic="t"))
            s.add(Workflow(id=1, name="default", is_default=True))
            await s.flush()
            run = WorkflowRun(
                id=1,
                workflow_id=1,
                project_id=34,
                nodes_snapshot=[{"id": "n_excel_gpt_1", "type": "excel_gpt"}],
            )
            s.add(run)
            await s.flush()
            s.add(
                NodeRun(
                    workflow_run_id=run.id,
                    node_key="n_excel_gpt_1",
                    node_type="excel_gpt",
                    status=NodeRunStatus.pending,
                )
            )
            if with_group:
                run.nodes_snapshot = [
                    {"id": "n_excel_gpt_1", "type": "excel_gpt"},
                    {"id": "n_excel_gpt_fw_script", "type": "excel_gpt"},
                ]
                s.add(
                    NodeRun(
                        workflow_run_id=run.id,
                        node_key="n_excel_gpt_fw_script",
                        node_type="excel_gpt",
                        status=NodeRunStatus.running,
                        progress=15,
                        progress_text="GPT",
                    )
                )
            await s.commit()

    await _seed(src_factory, with_group=True)
    await _seed(dest_factory, with_group=False)

    async with src_factory() as src, dest_factory() as dest:
        n = await _copy_noderuns_between_sessions(src, dest, 34)
        assert n >= 1
        await dest.commit()

    async with dest_factory() as dest:
        dest_run = (
            await dest.execute(
                select(WorkflowRun)
                .where(WorkflowRun.project_id == 34)
                .options(selectinload(WorkflowRun.node_runs))
            )
        ).scalar_one()
        keys = {nr.node_key: nr for nr in dest_run.node_runs}
        assert "n_excel_gpt_fw_script" in keys
        assert keys["n_excel_gpt_fw_script"].status is NodeRunStatus.running
        assert keys["n_excel_gpt_fw_script"].progress == 15
        snap_ids = {str(n.get("id")) for n in (dest_run.nodes_snapshot or [])}
        assert "n_excel_gpt_fw_script" in snap_ids

    await src_engine.dispose()
    await dest_engine.dispose()
