"""Тесты наследования meta и layout для batch-подпроектов."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sqlalchemy import select

from app.models import (
    Base,
    BatchProject,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
)
from app.services import batches as batches_svc
from app.services import sidebar_layout as layout_svc
from app.services.canvas_graph import build_canvas_graph_payload


@pytest.fixture
async def session(tmp_path, monkeypatch) -> AsyncSession:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    layout_svc.save_layout(layout_svc._empty_layout())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        wf = Workflow(
            name="default",
            is_default=True,
            nodes=[
                {"id": "plan", "type": "plan", "position": {"x": 0, "y": 0}},
                {"id": "script", "type": "script", "position": {"x": 200, "y": 0}},
            ],
            edges=[{"id": "e1", "source": "plan", "target": "script"}],
        )
        s.add(wf)
        await s.flush()
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_snapshot_meta_whitelist_only(session: AsyncSession) -> None:
    template = Project(
        slug="tpl",
        topic="Template",
        status=ProjectStatus.published,
        meta={
            "canvas_graph": build_canvas_graph_payload(
                workflow_id=1,
                nodes=[
                    {
                        "id": "plan",
                        "type": "plan",
                        "position": {"x": 1, "y": 2},
                        "data": {"status": "done", "progress": 100, "label": "Plan"},
                    }
                ],
                edges=[],
            ),
            "custom_prompts": {"plan": "hello"},
            "montage_board": {"clips": [1, 2]},
            "prompt_history": ["old"],
            "mass_bgm_enabled": True,
            "topic_card": {"title": "parent"},
        },
    )
    session.add(template)
    await session.flush()

    snap = batches_svc._snapshot_settings_from(template)
    assert "hero_mode" in snap
    meta = snap.get("meta") or {}
    assert "canvas_graph" in meta
    assert "custom_prompts" in meta
    assert "montage_board" not in meta
    assert "prompt_history" not in meta
    assert "mass_bgm_enabled" not in meta
    assert "topic_card" not in meta
    node_data = meta["canvas_graph"]["nodes"][0].get("data") or {}
    assert node_data.get("status") is None
    assert node_data.get("label") == "Plan"


@pytest.mark.asyncio
async def test_add_topics_preserves_topic_card_and_creates_run(
    session: AsyncSession,
) -> None:
    template = Project(
        slug="tpl2",
        topic="Tpl",
        status=ProjectStatus.new,
        meta={
            "canvas_graph": build_canvas_graph_payload(
                workflow_id=1,
                nodes=[{"id": "plan", "type": "plan", "position": {"x": 0, "y": 0}}],
                edges=[],
            ),
            "montage_board": {"x": 1},
        },
    )
    session.add(template)
    await session.flush()

    batch = await batches_svc.create_batch(
        session, name="Mass A", template_project_id=template.id
    )
    batch.meta = {"permanent_product": {"name": "Gadget", "description": "d"}}
    await session.flush()

    created = await batches_svc.add_topics(
        session,
        batch,
        [{"title": "Video 1", "style": "pop", "source": "xlsx"}],
    )
    assert len(created) == 1
    sub = created[0]
    assert sub.meta.get("topic_card", {}).get("title") == "Video 1"
    assert sub.meta.get("topic_card", {}).get("style") == "pop"
    assert sub.meta.get("permanent_product", {}).get("name") == "Gadget"
    assert "montage_board" not in (sub.meta or {})
    assert sub.meta.get("canvas_graph") is not None

    run = (
        await session.execute(
            select(WorkflowRun).where(WorkflowRun.project_id == sub.id)
        )
    ).scalar_one_or_none()
    assert run is not None
    await session.refresh(run, attribute_names=["node_runs"])
    assert len(run.node_runs) >= 1
    assert all(nr.status is NodeRunStatus.pending for nr in run.node_runs)

    folder_id = layout_svc.get_batch_folder_id(batch.id)
    assert folder_id is not None
    layout = layout_svc.load_layout()
    placement = layout["project_layout"].get(str(sub.id))
    assert placement is not None
    assert placement["folder_id"] == folder_id
    assert placement["order"] == sub.batch_position


@pytest.mark.asyncio
async def test_clean_subprojects_meta_strips_garbage(session: AsyncSession) -> None:
    batch = BatchProject(name="B", slug="b-clean", status="new")
    session.add(batch)
    await session.flush()
    sub = Project(
        slug="b-clean__001_topic",
        topic="Topic",
        status=ProjectStatus.new,
        batch_id=batch.id,
        batch_position=1,
        batch_slug=batch.slug,
        meta={
            "topic_card": {"title": "Topic"},
            "montage_board": {"a": 1},
            "auto_retry_count": 2,
            "mass_bgm_enabled": True,
            "custom_prompts": {"plan": "x"},
        },
    )
    session.add(sub)
    await session.flush()

    result = await batches_svc.clean_subprojects_meta(session)
    await session.refresh(sub)
    assert result["projects"] == 1
    assert "montage_board" not in sub.meta
    assert "auto_retry_count" not in sub.meta
    assert "mass_bgm_enabled" not in sub.meta
    assert sub.meta.get("custom_prompts") == {"plan": "x"}


@pytest.mark.asyncio
async def test_sync_projects_puts_batch_subs_in_folder(session: AsyncSession) -> None:
    batch = BatchProject(name="Sync Batch", slug="sync-b", status="new")
    session.add(batch)
    await session.flush()
    layout_svc.ensure_batch_folder(batch.id, batch.name)

    sub = Project(
        slug="sync-b__001_a",
        topic="A",
        status=ProjectStatus.new,
        batch_id=batch.id,
        batch_position=3,
        batch_slug=batch.slug,
        meta={"topic_card": {"title": "A"}},
    )
    session.add(sub)
    await session.flush()

    layout_svc.sync_projects(
        set(),
        batch_subprojects={sub.id: (batch.id, 3)},
        batch_names={batch.id: batch.name},
    )
    layout = layout_svc.load_layout()
    entry = layout["project_layout"][str(sub.id)]
    assert entry["folder_id"] == layout_svc.get_batch_folder_id(batch.id)
    assert entry["order"] == 3
