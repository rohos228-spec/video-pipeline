"""Порядок нод пайплайна: нельзя прыгать через шаги."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.orchestrator.graph.planner import assert_step_allowed_by_graph
from app.services.project_steps import start_step


@pytest.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _project(
    *,
    status: ProjectStatus,
    general_plan: str = "x" * 200,
    script_text: str = "",
    meta: dict | None = None,
) -> Project:
    return Project(
        slug="order-test",
        topic="t",
        status=status,
        general_plan=general_plan,
        script_text=script_text,
        meta=meta or {},
    )


@pytest.mark.asyncio
async def test_script_allowed_from_plan_ready(session, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _project(status=ProjectStatus.plan_ready)
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    (p.data_dir / "project.xlsx").write_bytes(b"x" * 2048)

    status = await start_step(session, p, "script", skip_queue_guard=True)
    assert status is ProjectStatus.scripting


@pytest.mark.asyncio
async def test_script_blocked_from_new(session) -> None:
    p = _project(status=ProjectStatus.new, general_plan="")
    session.add(p)
    await session.flush()
    with pytest.raises(ValueError, match="нельзя запустить"):
        await assert_step_allowed_by_graph(session, p, "script")


@pytest.mark.asyncio
async def test_img_blocked_from_plan_ready(session) -> None:
    p = _project(status=ProjectStatus.plan_ready)
    session.add(p)
    await session.flush()
    with pytest.raises(ValueError, match="предыдущие ноды"):
        await assert_step_allowed_by_graph(session, p, "img")


@pytest.mark.asyncio
async def test_anim_pr_blocked_before_images(session) -> None:
    p = _project(status=ProjectStatus.image_prompts_ready)
    session.add(p)
    await session.flush()
    with pytest.raises(ValueError, match="предыдущие ноды"):
        await assert_step_allowed_by_graph(session, p, "anim_pr")


@pytest.mark.asyncio
async def test_anim_pr_allowed_after_images_ready(session) -> None:
    p = _project(status=ProjectStatus.images_ready)
    session.add(p)
    await session.flush()
    await assert_step_allowed_by_graph(session, p, "anim_pr")


@pytest.mark.asyncio
async def test_img_allowed_after_image_prompts_ready(session) -> None:
    p = _project(status=ProjectStatus.image_prompts_ready)
    session.add(p)
    await session.flush()
    await assert_step_allowed_by_graph(session, p, "img")


@pytest.mark.asyncio
async def test_assemble_blocked_from_new(session) -> None:
    p = _project(status=ProjectStatus.new)
    session.add(p)
    await session.flush()
    with pytest.raises(ValueError, match="нельзя запустить"):
        await assert_step_allowed_by_graph(session, p, "assemble")


@pytest.mark.asyncio
async def test_custom_graph_bypass_allows_img_after_plan(session) -> None:
    """Кастомное ребро plan→images — порядок по графу, не по linear menu."""
    p = _project(
        status=ProjectStatus.plan_ready,
        meta={
            "canvas_graph": {
                "nodes": [
                    {"id": "n_plan", "type": "plan", "position": {"x": 0, "y": 0}, "data": {}},
                    {
                        "id": "n_images",
                        "type": "images",
                        "position": {"x": 300, "y": 0},
                        "data": {},
                    },
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "n_plan",
                        "target": "n_images",
                        "sourceHandle": "out",
                        "targetHandle": "in",
                    }
                ],
            }
        },
    )
    session.add(p)
    await session.flush()
    await assert_step_allowed_by_graph(session, p, "img")


@pytest.mark.asyncio
async def test_start_step_rejects_out_of_order_img(session, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _project(status=ProjectStatus.plan_ready)
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    (p.data_dir / "project.xlsx").write_bytes(b"x" * 2048)

    with pytest.raises(ValueError, match="предыдущие ноды"):
        await start_step(session, p, "img", skip_queue_guard=True)


@pytest.mark.asyncio
async def test_objects_wrapper_blocked_before_frames(session) -> None:
    p = _project(status=ProjectStatus.script_ready)
    session.add(p)
    await session.flush()
    with pytest.raises(ValueError, match="нельзя запустить"):
        await assert_step_allowed_by_graph(session, p, "objects")
