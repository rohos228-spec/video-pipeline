"""Тесты дочерних проектов."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus, Workflow
from app.services.mass_factory import mass_parent_id
from app.services.project_child import (
    ChildDataCopyJob,
    apply_child_data_copy,
    create_child_from_parent,
    finalize_child_data_dir,
)
from app.web.routers.projects import _slugify


@pytest.fixture
async def session(tmp_path, monkeypatch) -> AsyncSession:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        wf = Workflow(
            name="default",
            is_default=True,
            nodes=[{"id": "plan", "type": "plan", "position": {"x": 0, "y": 0}}],
            edges=[],
        )
        s.add(wf)
        await s.flush()
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_create_child_copies_script_and_parent_link(session: AsyncSession, tmp_path, monkeypatch) -> None:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    parent = Project(
        slug="parent-vo",
        topic="Родитель",
        status=ProjectStatus.script_ready,
        script_text="Закадровый текст для копии",
        general_plan="План ролика",
        hero_mode="no_hero",
        meta={"canvas_graph": {"nodes": [], "edges": []}},
    )
    session.add(parent)
    await session.flush()
    parent.data_dir.mkdir(parents=True, exist_ok=True)
    (parent.data_dir / "voiceover.txt").write_text("Закадровый текст для копии", encoding="utf-8")

    parent.auto_mode = True
    child = await create_child_from_parent(session, parent, slugify=_slugify)
    await session.commit()
    await session.refresh(child)
    await finalize_child_data_dir(parent, child)

    assert mass_parent_id(child) == parent.id
    assert child.script_text == parent.script_text
    assert child.general_plan == parent.general_plan
    assert child.status is ProjectStatus.script_ready
    assert child.auto_mode is False  # не конкурирует с генерацией родителя
    assert (child.data_dir / "voiceover.txt").read_text(encoding="utf-8") == parent.script_text
    assert isinstance(child.meta, dict) and child.meta.get("project_child_manual") is True


def test_apply_child_data_copy_skips_heavy_media(tmp_path: Path) -> None:
    src = tmp_path / "parent"
    dst = tmp_path / "child"
    (src / "videos").mkdir(parents=True)
    (src / "old" / "scenes").mkdir(parents=True)
    (src / "scenes").mkdir(parents=True)
    (src / "videos" / "clip.mp4").write_bytes(b"x" * 1000)
    (src / "old" / "scenes" / "a.png").write_bytes(b"png")
    (src / "scenes" / "frame.png").write_bytes(b"png")

    apply_child_data_copy(
        ChildDataCopyJob(
            src=src,
            dst=dst,
            topic="t",
            slug="s",
            hero_mode=None,
            status="script_ready",
            script_text="vo",
        )
    )

    assert (dst / "scenes" / "frame.png").is_file()
    assert (dst / "voiceover.txt").read_text(encoding="utf-8") == "vo"
    assert not (dst / "videos").exists()
    assert not (dst / "old" / "scenes" / "a.png").exists()
