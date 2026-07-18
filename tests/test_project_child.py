"""Тесты дочерних проектов."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus, Workflow
from app.services.mass_factory import mass_parent_id
from app.services.project_child import (
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
async def test_create_child_inherits_settings_not_content(
    session: AsyncSession, tmp_path, monkeypatch
) -> None:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    parent = Project(
        slug="parent-vo",
        topic="Родитель",
        status=ProjectStatus.script_ready,
        script_text="Закадровый текст НЕ копировать",
        general_plan="План НЕ копировать",
        hero_description="Герой НЕ копировать",
        hero_mode="no_hero",
        image_generator="gpt_image_2",
        aspect_ratio="9_16",
        video_generator="veo_3_fast",
        prompt_overrides={"plan": "my_plan.md", "use_blocks_v2": True},
        gpt_text_overrides={"plan": "Текст для GPT родителя"},
        auto_mode=True,
        meta={
            "canvas_graph": {"nodes": [{"id": "plan"}], "edges": []},
            "custom_prompts": {"plan": []},
            "mass_queue_topics": ["should-strip"],
        },
    )
    session.add(parent)
    await session.flush()
    parent.data_dir.mkdir(parents=True, exist_ok=True)
    (parent.data_dir / "voiceover.txt").write_text("vo parent", encoding="utf-8")
    (parent.data_dir / "project.xlsx").write_bytes(b"parent-xlsx")
    (parent.data_dir / "scenes").mkdir()
    (parent.data_dir / "scenes" / "frame.png").write_bytes(b"png")

    child = await create_child_from_parent(session, parent, slugify=_slugify)
    await session.commit()
    await session.refresh(child)
    await finalize_child_data_dir(parent, child)

    assert mass_parent_id(child) == parent.id
    assert child.status is ProjectStatus.new
    assert child.auto_mode is False
    assert child.script_text is None
    assert child.general_plan is None
    assert child.hero_description is None
    assert child.image_generator == "gpt_image_2"
    assert child.aspect_ratio == "9_16"
    assert child.video_generator == "veo_3_fast"
    assert child.prompt_overrides.get("plan") == "my_plan.md"
    assert child.gpt_text_overrides.get("plan") == "Текст для GPT родителя"
    assert isinstance(child.meta, dict)
    assert child.meta.get("project_child_manual") is True
    assert child.meta.get("canvas_graph") == {"nodes": [{"id": "plan"}], "edges": []}
    assert child.meta.get("custom_prompts") == {"plan": []}
    assert "mass_queue_topics" not in child.meta

    # Нет закадрового и результатов родителя; Excel — свежий шаблон.
    assert not (child.data_dir / "voiceover.txt").exists()
    assert not (child.data_dir / "scenes" / "frame.png").exists()
    assert (child.data_dir / "project.xlsx").is_file()
    assert (child.data_dir / "project.xlsx").read_bytes() != b"parent-xlsx"

