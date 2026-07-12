#!/usr/bin/env python3
"""Method 2: targeted logic verification for excel_gpt refactor."""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

# Repo root on path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models import Project, ProjectStatus
from app.orchestrator.default_graph import default_graph
from app.orchestrator.graph.planner import WorkflowGraph
from app.orchestrator.node_registry import spec_for_node, spec_for_step_code
from app.services.excel_gpt_node import (
    EXCEL_GPT_NODE_TYPE,
    assign_slot_indices,
    attachment_paths,
    display_attachment_name,
    migrate_enrich_nodes,
    slot_index_from_node,
)
from app.services.gpt_verdict_review import attachments_for_step


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def fail(msg: str) -> None:
    print(f"  FAIL {msg}")
    raise SystemExit(1)


def test_migration() -> None:
    print("[1] migrate enrich_* → excel_gpt")
    nodes = [
        {"id": "n_enrich_1", "type": "enrich_1", "position": {"x": 0, "y": 0}, "data": {}},
        {
            "id": "n_enrich_2",
            "type": "enrich_2",
            "position": {"x": 300, "y": 0},
            "data": {"label": "Доп работа с EXCEL #2"},
        },
        {"id": "n_enrich_3", "type": "enrich_3", "position": {"x": 600, "y": 0}, "data": {"label": "Custom"}},
    ]
    out = assign_slot_indices(migrate_enrich_nodes(nodes))
    types = {n["id"]: n["type"] for n in out}
    if types.get("n_enrich_1") != EXCEL_GPT_NODE_TYPE:
        fail("enrich_1 not migrated")
    if slot_index_from_node(next(n for n in out if n["id"] == "n_enrich_1")) != 1:
        fail("slot 1 wrong after migrate")
    if slot_index_from_node(next(n for n in out if n["id"] == "n_enrich_3")) != 3:
        fail("slot renumber left-to-right failed")
    legacy = next(n for n in out if n["id"] == "n_enrich_2")["data"].get("label")
    if legacy != "Работа с GPT":
        fail(f"legacy label not rewritten: {legacy!r}")
    custom = next(n for n in out if n["id"] == "n_enrich_3")["data"].get("label")
    if custom != "Custom":
        fail("custom label lost on migrate")
    ok("migration + slot assignment + legacy labels")


def test_attachments(tmp: Path) -> None:
    print("[2] attachment_paths per inputSource")
    p = Project(id=99, slug="verify", topic="t", status=ProjectStatus.new)
    base = Path(__file__).resolve().parents[1]
    import os
    os.environ.setdefault("DATA_DIR", str(tmp / "data"))
    from app.settings import settings
    settings.data_dir = str(tmp / "data")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    (p.data_dir / "project.xlsx").write_bytes(b"x" * 2048)
    (p.data_dir / "voiceover.txt").write_text("vo", encoding="utf-8")
    nk = "n_excel_gpt_1"
    up_dir = p.data_dir / "excel_gpt_uploads" / nk
    up_dir.mkdir(parents=True)
    (up_dir / "my.xlsx").write_bytes(b"y" * 2048)
    p.meta = {
        "excel_gpt_nodes": {
            nk: {
                "inputSource": "upload",
                "uploadedFileName": "my.xlsx",
                "label": "my.xlsx",
            }
        },
        "active_excel_gpt_node_key": nk,
    }
    paths = attachment_paths(p, nk)
    if len(paths) != 1 or paths[0].name != "my.xlsx":
        fail(f"upload attachment wrong: {paths}")
    ok("upload source")
    p.meta["excel_gpt_nodes"][nk]["inputSource"] = "voiceover"
    paths = attachment_paths(p, nk)
    if len(paths) != 1 or paths[0].name != "voiceover.txt":
        fail(f"voiceover attachment wrong: {paths}")
    ok("voiceover source")
    p.meta["excel_gpt_nodes"][nk]["inputSource"] = "project_xlsx"
    if display_attachment_name(p, nk) != "project.xlsx":
        fail("display name for project_xlsx")
    ok("project_xlsx display name")


async def test_planner_states() -> None:
    print("[3] planner derived_node_states for 2 excel_gpt nodes")
    nodes, edges = default_graph()
    g = WorkflowGraph(nodes, edges)
    p = Project(id=1, slug="g", topic="t", status=ProjectStatus.enriching_2)
    p.meta = {"enrich_completed_slots": [1], "graph_executor": True}
    states = g.derived_node_states(p)
    k1 = "n_excel_gpt_1"
    k2 = "n_excel_gpt_2"
    if states.get(k1) != "done" and str(states.get(k1)) != "NodeRunStatus.done":
        # enum may compare as enum
        from app.models import NodeRunStatus

        if states.get(k1) != NodeRunStatus.done:
            fail(f"slot1 should be done, got {states.get(k1)}")
    from app.models import NodeRunStatus

    if states.get(k2) != NodeRunStatus.running:
        fail(f"slot2 should be running, got {states.get(k2)}")
    ok("per-slot running/done states")


def test_spec_step_code() -> None:
    print("[4] spec_for_step_code('excel_gpt')")
    spec = spec_for_step_code("excel_gpt")
    if spec is None or spec.step_code != "excel_gpt":
        fail("excel_gpt step spec missing")
    nodes, _ = default_graph()
    n2 = next(n for n in nodes if n["id"] == "n_excel_gpt_2")
    sp = spec_for_node(n2)
    if sp is None or sp.running_status != ProjectStatus.enriching_2:
        fail(f"slot2 spec wrong: {sp}")
    ok("step + per-node spec")


async def test_attachments_api_shape(tmp: Path) -> None:
    print("[5] attachments_for_step with node_key")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        p = Project(id=1, slug="api", topic="t", status=ProjectStatus.new)
        from app.settings import settings
        settings.data_dir = str(tmp / "data")
        p.data_dir.mkdir(parents=True, exist_ok=True)
        (p.data_dir / "project.xlsx").write_bytes(b"x" * 2048)
        nk = "n_excel_gpt_1"
        p.meta = {
            "excel_gpt_nodes": {
                nk: {"inputSource": "project_xlsx", "label": "Работа с GPT"},
            }
        }
        session.add(p)
        await session.commit()
        files = await attachments_for_step(session, p, "excel_gpt", node_key=nk)
        if not files or files[0].name != "project.xlsx":
            fail(f"attachments_for_step: {files}")
    ok("attachments_for_step node_key")


async def test_reset_meta_clears_slots() -> None:
    print("[6] reset meta clears enrich_completed_slots")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base
    from app.services.excel_gpt_node import clear_slot_completion_meta

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    nodes, _ = default_graph()
    async with Session() as session:
        p = Project(id=2, slug="reset", topic="t", status=ProjectStatus.enrich_2_ready)
        p.meta = {
            "enrich_completed_slots": [1, 2],
            "excel_gpt_completed_keys": ["n_excel_gpt_1", "n_excel_gpt_2"],
            "active_excel_gpt_node_key": "n_excel_gpt_2",
            "graph_executor": True,
        }
        session.add(p)
        from app.models import Workflow, WorkflowRun

        wf = Workflow(name="t", nodes=nodes, edges=[], is_default=False)
        session.add(wf)
        await session.flush()
        run = WorkflowRun(
            workflow_id=wf.id,
            project_id=p.id,
            nodes_snapshot=nodes,
            edges_snapshot=[],
        )
        session.add(run)
        await session.commit()
        res = await clear_slot_completion_meta(session, p, 2, node_key="n_excel_gpt_2")
        await session.commit()
        await session.refresh(p)
        meta = p.meta or {}
        if 2 in (meta.get("enrich_completed_slots") or []):
            fail("enrich_completed_slots still has slot 2")
        if "n_excel_gpt_2" in (meta.get("excel_gpt_completed_keys") or []):
            fail("excel_gpt_completed_keys still has n_excel_gpt_2")
        if res.get("slots_cleared") != [2]:
            fail(f"unexpected slots_cleared: {res}")
    ok("reset meta slot 2")


def main() -> None:
    print("=== excel_gpt logic verification ===")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_migration()
        test_attachments(tmp)
        asyncio.run(test_planner_states())
        test_spec_step_code()
        asyncio.run(test_attachments_api_shape(tmp))
        asyncio.run(test_reset_meta_clears_slots())
    print("=== ALL CHECKS PASSED ===")


if __name__ == "__main__":
    main()
