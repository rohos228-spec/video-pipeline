"""Master state.db vs project.db: ▶ не должен крутить ноду вхолостую."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.project_db import (
    close_all_project_engines,
    get_project_sessionmaker,
    init_project_db,
    pull_master_runtime_into_project,
    push_runtime_to_project_db,
)
from app.services.project_steps import start_step


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
