"""Дочерний проект должен резолвить промты родителя, а не default.md."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.prompt_library import (
    _clean_variant_name,
    resolve_project_prompt_with_source,
    write_prompt,
)


def test_clean_variant_name_strips_md_suffix() -> None:
    assert _clean_variant_name("my_plan.md") == "my_plan"
    assert _clean_variant_name("My_Plan.MD") == "My_Plan"
    assert _clean_variant_name("steven.txt.md") == "steven.txt"
    assert _clean_variant_name("plain") == "plain"


def test_override_with_md_suffix_resolves_file(tmp_path: Path, monkeypatch) -> None:
    prompts_root = tmp_path / "prompts"
    monkeypatch.setattr("app.services.prompt_library.PROMPTS_ROOT", prompts_root)
    monkeypatch.setattr("app.services.prompt_active_global.PROMPTS_ROOT", prompts_root)
    write_prompt("plan", "parent_custom", "PARENT CUSTOM")

    name, source = resolve_project_prompt_with_source(
        {"plan": "parent_custom.md"},
        "plan",
        meta={},
    )
    assert (name, source) == ("parent_custom", "override")


def test_inherited_slot_beats_global_default(tmp_path: Path, monkeypatch) -> None:
    """Ребёнок копирует prompt_slot_variants; global=default не должен побеждать."""
    prompts_root = tmp_path / "prompts"
    monkeypatch.setattr("app.services.prompt_library.PROMPTS_ROOT", prompts_root)
    monkeypatch.setattr("app.services.prompt_active_global.PROMPTS_ROOT", prompts_root)
    write_prompt("plan", "default", "# default")
    write_prompt("plan", "parent_plan", "FROM PARENT")
    from app.services.prompt_active_global import set_global_active

    set_global_active("plan", "default")

    meta = {"prompt_slot_variants": {"n_plan": {"main": "parent_plan"}}}
    name, source = resolve_project_prompt_with_source(
        {},  # как если override сломан/пуст
        "plan",
        meta=meta,
    )
    assert (name, source) == ("parent_plan", "slot")


def test_child_md_override_plus_global_default_still_uses_parent(
    tmp_path: Path, monkeypatch
) -> None:
    """Типичный баг: override сохранён как name.md, global=default."""
    prompts_root = tmp_path / "prompts"
    monkeypatch.setattr("app.services.prompt_library.PROMPTS_ROOT", prompts_root)
    monkeypatch.setattr("app.services.prompt_active_global.PROMPTS_ROOT", prompts_root)
    write_prompt("plan", "default", "# default")
    write_prompt("plan", "my_plan", "CUSTOM")
    from app.services.prompt_active_global import set_global_active

    set_global_active("plan", "default")

    meta = {"prompt_slot_variants": {"n_plan": {"main": "my_plan"}}}
    name, source = resolve_project_prompt_with_source(
        {"plan": "my_plan.md"},
        "plan",
        meta=meta,
    )
    assert (name, source) == ("my_plan", "override")


def test_excel_gpt_inherited_slots_beat_global_without_node_key() -> None:
    meta = {
        "prompt_slot_variants": {
            "n_excel_gpt_1": {"main": "custom_excel"},
        }
    }
    with patch(
        "app.services.prompt_library.excel_gpt_prompt_exists",
        side_effect=lambda name: name in {"custom_excel", "default"},
    ), patch(
        "app.services.prompt_active_global.get_global_active",
        return_value="default",
    ):
        name, source = resolve_project_prompt_with_source(
            {}, "excel_gpt", meta=meta
        )
    assert (name, source) == ("custom_excel", "slot")


@pytest.mark.asyncio
async def test_create_child_resolves_parent_plan_prompt(
    tmp_path, monkeypatch
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Project, ProjectStatus, Workflow
    from app.services.project_child import create_child_from_parent
    from app.services.prompt_library import read_resolved_project_prompt
    from app.web.routers.projects import _slugify

    from app import settings as app_settings

    prompts_root = tmp_path / "prompts"
    monkeypatch.setattr("app.services.prompt_library.PROMPTS_ROOT", prompts_root)
    monkeypatch.setattr("app.services.prompt_active_global.PROMPTS_ROOT", prompts_root)
    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    write_prompt("plan", "default", "# default plan")
    write_prompt("plan", "factory_plan", "FACTORY PLAN BODY")
    from app.services.prompt_active_global import set_global_active

    set_global_active("plan", "default")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Workflow(
                name="default",
                is_default=True,
                nodes=[{"id": "n_plan", "type": "plan", "position": {"x": 0, "y": 0}}],
                edges=[],
            )
        )
        parent = Project(
            slug="parent-prompts",
            topic="Родитель",
            status=ProjectStatus.new,
            hero_mode="no_hero",
            prompt_overrides={"plan": "factory_plan.md"},
            meta={
                "prompt_slot_variants": {"n_plan": {"main": "factory_plan"}},
                "custom_prompts": {},
            },
        )
        session.add(parent)
        await session.flush()

        child = await create_child_from_parent(session, parent, slugify=_slugify)
        await session.commit()
        await session.refresh(child)

        assert child.prompt_overrides.get("plan") == "factory_plan.md"
        name, _path, text, source = read_resolved_project_prompt(child, "plan")
        assert name == "factory_plan"
        assert source == "override"
        assert "FACTORY PLAN BODY" in text
    await engine.dispose()
