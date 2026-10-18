"""Помеченная сущность не попадает в реестр, карточки GPT и платные описания."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Entity, Project, ProjectStatus
from app.orchestrator.steps.generate_items import _resolve_item_descriptions
from app.services.excel_characters import characters_from_entities, entity_cards_for_gpt
from app.services.scene_character_match import load_character_registry
from app.services.vision_check_db import load_character_rows


@pytest.mark.asyncio
async def test_marked_entity_is_hidden_from_llm_and_paid_readers() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        project = Project(slug="marked-entity", topic="t", status=ProjectStatus.frames_ready)
        session.add(project)
        await session.flush()
        live = Entity(
            project_id=project.id,
            type="character",
            code="c01",
            name="Живой",
            sort_key=1,
            attrs={"look": "плащ"},
        )
        marked = Entity(
            project_id=project.id,
            type="character",
            code="c02",
            name="Скрытый",
            sort_key=2,
            attrs={"look": "маска", "удалён": True, "archived": True, "активен": False},
        )
        live_item = Entity(
            project_id=project.id,
            type="prop",
            code="p01",
            name="Ключ",
            sort_key=3,
            attrs={"description": "медный"},
        )
        marked_item = Entity(
            project_id=project.id,
            type="prop",
            code="p02",
            name="Печать",
            sort_key=4,
            attrs={"description": "сургуч", "archived": True},
        )
        session.add_all([live, marked, live_item, marked_item])
        await session.flush()

        registry = await load_character_registry(session, project.id)
        assert [row.code for row in registry] == ["c01"]

        cards = characters_from_entities([live, marked])
        assert [card.id for card in cards] == ["c01"]
        gpt = entity_cards_for_gpt([live, marked])
        assert [card["id"] for card in gpt] == ["c01"]
        assert all(card["имя"] != "Скрытый" for card in gpt)

        rows = await load_character_rows(session, project)
        assert [row["id"] for row in rows] == ["c01"]

        items = await _resolve_item_descriptions(session, project)
        assert items == ["Ключ: медный"]
        assert all("Печать" not in text for text in items)
    await engine.dispose()
