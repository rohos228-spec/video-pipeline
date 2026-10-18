"""Ревью #352 r4 п.7: архивный персонаж не попадает в меню и промты.

Недозакрыто после e8fca8c2: карточку архивировали, но читатели
промтов, меню шотов и экспорт флаг не смотрели.
"""

import uuid
from contextlib import asynccontextmanager

import pytest
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Entity, Project, ProjectStatus
from app.services.apply_ops_batches import _GATE_CHARACTERS, set_shot_characters
from app.services.db_apply import _write_persons_sheet, upsert_characters
from app.services.db_v2 import entity_is_archived
from app.services.excel_characters import entity_cards_for_gpt


@pytest.fixture
async def mem():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def scope():
        async with factory() as session:
            yield session
            await session.commit()

    yield scope
    await engine.dispose()


@pytest.mark.asyncio
async def test_archived_character_leaves_menu_and_returns_active(mem) -> None:
    async with mem() as session:
        project = Project(
            slug=f"c-{uuid.uuid4().hex[:6]}",
            topic="t",
            status=ProjectStatus.frames_ready,
        )
        session.add(project)
        await session.flush()
        session.add(
            Entity(
                project_id=project.id,
                type="character",
                code="c01",
                name="Анна",
                attrs={"look": "светлые волосы", "hero_reference": "anna.png"},
            )
        )
        session.add(
            Entity(
                project_id=project.id,
                type="character",
                code="c02",
                name="Борис",
                attrs={"look": "серое пальто", "hero_reference": "boris.png"},
            )
        )
        await session.flush()

        await upsert_characters(
            session,
            project,
            [{"id": "c01", "имя": "Анна", "внешность": "светлые волосы"}],
            replace=True,
        )
        rows = list(
            (await session.execute(select(Entity).where(Entity.project_id == project.id))).scalars()
        )
        by = {r.code: r for r in rows}
        assert entity_is_archived(by["c02"]) is True
        assert by["c02"].attrs.get("inactive") is True
        assert by["c02"].attrs.get("hero_reference") == "boris.png"
        menu = entity_cards_for_gpt(rows)
        assert [card["id"] for card in menu] == ["c01"]

        await upsert_characters(
            session,
            project,
            [
                {"id": "c01", "имя": "Анна", "внешность": "светлые волосы"},
                {"id": "c02", "имя": "Борис", "внешность": "серое пальто"},
            ],
            replace=True,
        )
        rows = list(
            (await session.execute(select(Entity).where(Entity.project_id == project.id))).scalars()
        )
        by = {r.code: r for r in rows}
        assert entity_is_archived(by["c02"]) is False
        assert "archived" not in (by["c02"].attrs or {})
        assert "inactive" not in (by["c02"].attrs or {})
        assert by["c02"].attrs.get("hero_reference") == "boris.png"
        menu = entity_cards_for_gpt(rows)
        assert [card["id"] for card in menu] == ["c01", "c02"]


def test_shot_menu_and_export_skip_archived() -> None:
    set_shot_characters(
        [
            {"id": "c01", "имя": "Анна"},
            {"id": "c02", "имя": "Борис", "archived": True, "inactive": True},
        ]
    )
    assert [card["id"] for card in _GATE_CHARACTERS.get()] == ["c01"]

    class _Ent:
        def __init__(self, code: str, *, archived: bool) -> None:
            self.type = "character"
            self.code = code
            self.name = code
            self.sort_key = 1
            self.attrs = {
                "look": "вид",
                "hero_reference": f"{code}.png",
                **({"archived": True, "inactive": True} if archived else {}),
            }

    wb = Workbook()
    n = _write_persons_sheet(wb, [_Ent("c01", archived=False), _Ent("c02", archived=True)])
    ws = wb["Персонажи"]
    ids = [ws.cell(row=1, column=col).value for col in range(2, 6)]
    assert ids[0] == "c01"
    assert "c02" not in ids
    assert n > 0
