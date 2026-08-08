"""Группы нод: каталог + вставка веера scene_design в канвас проекта."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.services.node_groups import (
    get_node_group,
    insert_node_group,
    list_node_groups,
)


@pytest.fixture
async def mem_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("app.db.session_scope", _scope)
    yield _scope
    await engine.dispose()


def _linear_canvas() -> tuple[list[dict], list[dict]]:
    """topic → plan → script → split → hero (позиции как в default_graph)."""
    kinds = ["topic", "plan", "script", "split", "hero"]
    nodes = [
        {
            "id": f"n_{k}",
            "type": k,
            "position": {"x": 80.0 + i * 290.0, "y": 200.0},
            "data": {"label": k},
        }
        for i, k in enumerate(kinds)
    ]
    edges = [
        {"id": f"e_{i}", "source": f"n_{a}", "target": f"n_{b}"}
        for i, (a, b) in enumerate(zip(kinds, kinds[1:], strict=False))
    ]
    return nodes, edges


async def _mk_project(session: AsyncSession, *, with_canvas: bool = True) -> Project:
    nodes, edges = _linear_canvas()
    wf = Workflow(
        name=f"wf-{uuid.uuid4().hex[:8]}",
        is_default=True,
        nodes=nodes,
        edges=edges,
    )
    session.add(wf)
    await session.flush()
    meta: dict = {}
    if with_canvas:
        meta["canvas_graph"] = {
            "workflow_id": wf.id,
            "nodes": nodes,
            "edges": edges,
        }
    project = Project(
        slug=f"grp-{uuid.uuid4().hex[:8]}",
        topic="t",
        status=ProjectStatus.new,
        meta=meta,
    )
    session.add(project)
    await session.flush()
    session.add(
        WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.new,
            nodes_snapshot=nodes,
            edges_snapshot=edges,
        )
    )
    await session.commit()
    return project


def test_catalog_has_scene_fanout() -> None:
    groups = list_node_groups()
    fan = next(g for g in groups if g["id"] == "scene_design_fanout")
    assert fan["category"] == "planning"
    assert fan["node_count"] == 6
    assert fan["default_after_type"] == "split"
    keys = {n["key"] for n in fan["nodes"]}
    assert keys == {"characters", "world", "style", "camera", "action", "assemble"}
    grp = get_node_group("scene_design_fanout")
    assert grp is not None
    assert grp.project_meta.get("scene_design_enabled") is True
    for spec in grp.nodes:
        assert spec.prompt_variant and spec.prompt_variant.startswith("sd_")


async def test_insert_fanout_after_split(mem_db) -> None:
    async with mem_db() as session:
        project = await _mk_project(session)
        res = await insert_node_group(session, project, "scene_design_fanout")

    assert res["after"] == "n_split"
    assert len(res["nodes"]) == 6
    cg = project.meta["canvas_graph"]
    by_id = {n["id"]: n for n in cg["nodes"]}
    split_x = by_id["n_split"]["position"]["x"]
    split_y = by_id["n_split"]["position"]["y"]
    for agent in ("characters", "world", "style", "camera", "action"):
        nid = f"n_excel_gpt_sd_{agent}"
        n = by_id[nid]
        assert n["type"] == "excel_gpt"
        assert n["data"]["sd_agent"] == agent
        assert "slotIndex" not in n["data"]  # веер слоты enrich не занимает
        assert n["position"]["x"] == split_x + 290.0
        assert project.meta["prompt_slot_variants"][nid] == {"main": f"sd_{agent}"}
    asm = by_id["n_excel_gpt_sd_asm"]
    assert asm["data"]["sd_agent"] == "assemble"
    assert asm["position"]["x"] == split_x + 580.0
    assert asm["position"]["y"] == split_y

    pairs = {(e["source"], e["target"]) for e in cg["edges"]}
    assert ("n_split", "n_hero") not in pairs  # перешито
    for agent in ("characters", "world", "style", "camera", "action"):
        assert ("n_split", f"n_excel_gpt_sd_{agent}") in pairs
        assert (f"n_excel_gpt_sd_{agent}", "n_excel_gpt_sd_asm") in pairs
    assert ("n_excel_gpt_sd_asm", "n_hero") in pairs

    assert project.meta["scene_design_enabled"] is True

    # NodeRun'ы созданы с эффективными типами (виртуальные sd_agent/sd_assemble).
    async with mem_db() as session:
        from sqlalchemy import select

        from app.models import NodeRun, WorkflowRun

        run = (
            await session.execute(
                select(WorkflowRun).where(WorkflowRun.project_id == project.id)
            )
        ).scalars().first()
        assert run is not None
        nrs = (
            (
                await session.execute(
                    select(NodeRun).where(NodeRun.workflow_run_id == run.id)
                )
            )
            .scalars()
            .all()
        )
        by_key = {nr.node_key: nr.node_type for nr in nrs}
        assert by_key["n_excel_gpt_sd_camera"] == "sd_agent"
        assert by_key["n_excel_gpt_sd_asm"] == "sd_assemble"


async def test_insert_duplicate_raises(mem_db) -> None:
    async with mem_db() as session:
        project = await _mk_project(session)
        await insert_node_group(session, project, "scene_design_fanout")
        with pytest.raises(ValueError, match="уже на канвасе"):
            await insert_node_group(session, project, "scene_design_fanout")


async def test_insert_explicit_after(mem_db) -> None:
    async with mem_db() as session:
        project = await _mk_project(session)
        res = await insert_node_group(
            session, project, "scene_design_fanout", after="n_plan"
        )
    assert res["after"] == "n_plan"
    cg = project.meta["canvas_graph"]
    pairs = {(e["source"], e["target"]) for e in cg["edges"]}
    assert ("n_plan", "n_script") not in pairs
    assert ("n_plan", "n_excel_gpt_sd_characters") in pairs
    assert ("n_excel_gpt_sd_asm", "n_script") in pairs


async def test_insert_without_canvas_uses_default_workflow(mem_db) -> None:
    async with mem_db() as session:
        project = await _mk_project(session, with_canvas=False)
        res = await insert_node_group(session, project, "scene_design_fanout")
    assert res["after"] == "n_split"
    cg = project.meta["canvas_graph"]
    assert cg["workflow_id"]  # не 0 — фронт иначе отбросит граф
    assert any(n["id"] == "n_excel_gpt_sd_asm" for n in cg["nodes"])
