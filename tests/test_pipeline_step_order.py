"""Ручной запуск нод: порядок пайплайна не блокирует start_step."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus
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
async def test_assert_step_allowed_is_noop(session) -> None:
    p = _project(status=ProjectStatus.hero_ready)
    session.add(p)
    await session.flush()
    # Раньше split блокировался при hero_ready — теперь всегда ok.
    await assert_step_allowed_by_graph(session, p, "split")
    await assert_step_allowed_by_graph(session, p, "img")
    await assert_step_allowed_by_graph(session, p, "assemble")


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
async def test_split_allowed_from_hero_ready(session, tmp_path, monkeypatch) -> None:
    """Регресс: «Разбивка на блоки» должна стартовать даже при hero_ready."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _project(status=ProjectStatus.hero_ready, script_text="script text")
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    (p.data_dir / "project.xlsx").write_bytes(b"x" * 2048)

    status = await start_step(
        session, p, "split", skip_queue_guard=True, explicit_ui_start=True
    )
    assert status is ProjectStatus.splitting


@pytest.mark.asyncio
async def test_img_allowed_from_plan_ready(session, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _project(status=ProjectStatus.plan_ready)
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    session.add(
        Frame(
            project_id=p.id,
            number=1,
            voiceover_text="voiceover for frame",
            image_prompt="prompt",
        )
    )
    await session.flush()

    status = await start_step(
        session, p, "img", skip_queue_guard=True, explicit_ui_start=True
    )
    assert status is ProjectStatus.generating_images
