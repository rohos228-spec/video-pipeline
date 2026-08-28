"""Seed-ячейка: целый закадр → 1 Frame для fw_script."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services import db_v2


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _mk_project(session: AsyncSession, *, script: str = "") -> Project:
    p = Project(slug="seed-vo", topic="тест", status=None)  # type: ignore[arg-type]
    p.script_text = script
    session.add(p)
    await session.flush()
    return p


async def test_seed_from_empty_frames(session: AsyncSession) -> None:
    full = "Первая фраза целиком. Вторая мысль дальше по тексту."
    p = await _mk_project(session, script=full)
    fr = await db_v2.ensure_single_seed_vo_cell(session, p, full)
    await session.commit()

    frames = list(
        (await session.execute(select(Frame).where(Frame.project_id == p.id))).scalars()
    )
    assert len(frames) == 1
    assert frames[0].id == fr.id
    assert frames[0].uuid
    assert frames[0].voiceover_text == full
    assert frames[0].number == 1


async def test_seed_replaces_split_cells(session: AsyncSession) -> None:
    full = "Целый закадр одним куском без разбивки на короткие блоки."
    p = await _mk_project(session, script=full)
    for i in range(1, 4):
        session.add(
            Frame(
                project_id=p.id,
                number=i,
                voiceover_text=f"кусок {i}",
                attrs={},
            )
        )
    await session.commit()

    fr = await db_v2.ensure_single_seed_vo_cell(session, p, full)
    await session.commit()

    frames = list(
        (await session.execute(select(Frame).where(Frame.project_id == p.id))).scalars()
    )
    assert len(frames) == 1
    assert frames[0].voiceover_text == full
    assert frames[0].uuid == fr.uuid


async def test_seed_idempotent_same_text(session: AsyncSession) -> None:
    full = "Один и тот же целый закадр."
    p = await _mk_project(session, script=full)
    a = await db_v2.ensure_single_seed_vo_cell(session, p, full)
    await session.commit()
    b = await db_v2.ensure_single_seed_vo_cell(session, p, full)
    await session.commit()
    assert a.id == b.id
    assert a.uuid == b.uuid


def test_resolve_full_voiceover_prefers_script_text() -> None:
    p = Project(slug="vo-res", topic="t", status=None)  # type: ignore[arg-type]
    p.script_text = "  Целый   текст  "
    assert db_v2.resolve_full_voiceover_text(p) == "Целый текст"


def test_resolve_full_voiceover_empty() -> None:
    p = Project(slug="vo-empty", topic="t", status=None)  # type: ignore[arg-type]
    p.script_text = ""
    assert db_v2.resolve_full_voiceover_text(p) == ""


@pytest.mark.asyncio
async def test_apply_ops_script_kind_writes_bits(session: AsyncSession) -> None:
    from app.services.db_apply import apply_ops

    full = "Целый закадр для разметки битов одним куском."
    p = await _mk_project(session, script=full)
    fr = await db_v2.ensure_single_seed_vo_cell(session, p, full)
    bits = [{"порядок": 1, "суть": "было"}, {"порядок": 2, "суть": "стало"}]
    await apply_ops(
        session,
        p,
        [
            {
                "frame_uuid": fr.uuid,
                "fields": {
                    "биты": bits,
                    "закадр": "нельзя",
                    "промт_картинки": "нет",
                },
            }
        ],
        node_kind="excel_gpt_script",
    )
    await session.refresh(fr)
    assert fr.voiceover_text == full
    assert fr.image_prompt in (None, "")
    assert fr.attrs["биты"] == bits
