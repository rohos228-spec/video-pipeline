"""Привязка уникального old/*_result_*.xlsx к node_key."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook

from app.services.node_xlsx_snapshot import (
    META_KEY,
    bind_snapshot_entry,
    resolve_bound_xlsx_path,
    snapshot_and_bind_node_xlsx,
    snapshots_map,
)
from app.services.xlsx_versioning import snapshot_node_result_xlsx


def _write_xlsx(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws["A1"] = marker
    wb.save(path)


def test_snapshot_node_result_xlsx_unique_name(tmp_path: Path) -> None:
    xlsx = tmp_path / "project.xlsx"
    _write_xlsx(xlsx, "live")
    snap = snapshot_node_result_xlsx(xlsx, node_key="n_plan")
    assert snap is not None
    assert snap.parent.name == "old"
    assert "n_plan" in snap.name
    assert "_result_" in snap.name
    assert snap.name.endswith(".xlsx")
    # Уникальность при повторном снимке в ту же секунду.
    snap2 = snapshot_node_result_xlsx(xlsx, node_key="n_plan")
    assert snap2 is not None
    assert snap2 != snap
    assert snap2.exists()


@pytest.mark.asyncio
async def test_snapshot_and_bind_then_preview_by_node_key(
    tmp_path: Path, monkeypatch
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Project, ProjectStatus, Workflow
    from app.settings import settings
    from app.web.routers import project_ops

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    data_dir = tmp_path / "videos" / "slug-snap"
    data_dir.mkdir(parents=True)

    live = data_dir / "project.xlsx"
    _write_xlsx(live, "AFTER_ENRICH")

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'snap.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Workflow(name="default", is_default=True, nodes=[], edges=[]))
        p = Project(
            topic="t",
            slug="slug-snap",
            status=ProjectStatus.new,
            hero_mode="no_hero",
        )
        session.add(p)
        await session.flush()

        # Снимок «как после enrich»
        entry = await snapshot_and_bind_node_xlsx(
            session, p, node_key="n_excel_gpt_1"
        )
        assert entry is not None
        assert entry["name"].endswith(".xlsx")
        assert "n_excel_gpt_1" in entry["name"]
        await session.commit()
        await session.refresh(p)

        assert "n_excel_gpt_1" in snapshots_map(p)
        bound = resolve_bound_xlsx_path(p, "n_excel_gpt_1")
        assert bound is not None
        assert bound.name == entry["name"]

        # Live файл меняем — нода должна видеть старый снимок.
        _write_xlsx(live, "NEWER_LIVE")

        preview_live = await project_ops.preview_xlsx(
            project_id=p.id,
            sheet=None,
            max_rows=20,
            max_cols=10,
            start_row=1,
            row=None,
            raw=True,
            node_key=None,
            session=session,
        )
        assert preview_live["rows"][0][0] == "NEWER_LIVE"
        assert preview_live.get("xlsx_snapshot") is None

        preview_node = await project_ops.preview_xlsx(
            project_id=p.id,
            sheet=None,
            max_rows=20,
            max_cols=10,
            start_row=1,
            row=None,
            raw=True,
            node_key="n_excel_gpt_1",
            session=session,
        )
        assert preview_node["rows"][0][0] == "AFTER_ENRICH"
        assert preview_node["xlsx_snapshot"] == entry["name"]


def test_bind_two_nodes_keep_distinct_files(tmp_path: Path, monkeypatch) -> None:
    from app.models import Project, ProjectStatus
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    data_dir = tmp_path / "videos" / "slug-two"
    data_dir.mkdir(parents=True)
    live = data_dir / "project.xlsx"
    _write_xlsx(live, "A")

    p = Project(
        topic="t",
        slug="slug-two",
        status=ProjectStatus.new,
        hero_mode="no_hero",
    )
    # data_dir property uses settings — ensure slug path matches.
    assert p.data_dir == data_dir

    snap_a = snapshot_node_result_xlsx(live, node_key="n_plan")
    assert snap_a is not None
    bind_snapshot_entry(p, "n_plan", snap_a)

    _write_xlsx(live, "B")
    snap_b = snapshot_node_result_xlsx(live, node_key="n_split")
    assert snap_b is not None
    bind_snapshot_entry(p, "n_split", snap_b)

    assert META_KEY in (p.meta or {})
    path_a = resolve_bound_xlsx_path(p, "n_plan")
    path_b = resolve_bound_xlsx_path(p, "n_split")
    assert path_a is not None and path_b is not None
    assert path_a != path_b
    assert path_a.name != path_b.name
