"""▶ на project.db не должен требовать workflow-каталог внутри project.db."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus, Workflow
from app.project_db import (
    close_all_project_engines,
    get_project_sessionmaker,
    init_project_db,
)
from app.services.run_sync import _get_default_workflow_id, ensure_run_for_project


@pytest.mark.asyncio
async def test_default_workflow_falls_back_to_master_from_project_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with master_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    master_factory = async_sessionmaker(master_engine, expire_on_commit=False)

    async with master_factory() as ms:
        wf = Workflow(
            name="default",
            is_default=True,
            nodes=[{"id": "n1", "type": "plan", "position": {"x": 0, "y": 0}}],
            edges=[],
        )
        ms.add(wf)
        await ms.flush()
        wf_id = int(wf.id)
        await ms.commit()

    @asynccontextmanager
    async def master_scope():
        async with master_factory() as s:
            yield s

    monkeypatch.setattr("app.db.session_scope", master_scope)
    monkeypatch.setattr("app.services.run_sync.session_scope", master_scope)

    p = Project(
        id=880,
        slug="wf-isol",
        title="wf",
        topic="t",
        status=ProjectStatus.script_ready,
        meta={"canvas_graph": {"nodes": [{"id": "n1", "type": "plan"}], "edges": []}},
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    await init_project_db(p.data_dir, project=p)

    sm = await get_project_sessionmaker(p.data_dir)
    async with sm() as session:
        assert await session.get(Workflow, wf_id) is None
        found = await _get_default_workflow_id(session)
        assert found == wf_id
        run_id = await ensure_run_for_project(880, wf_id, session=session)
        assert run_id > 0
        assert await session.get(Workflow, wf_id) is not None

    await master_engine.dispose()
    await close_all_project_engines()
