"""DB v2: миграция, backfill, дробный порядок, версии промтов, граф."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameEdge, FrameText, Project, PromptVersion, Scene
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


async def _mk_project_with_frames(session: AsyncSession, n: int = 3) -> Project:
    p = Project(slug="v2test", topic="тест", status=None)  # type: ignore[arg-type]
    session.add(p)
    await session.flush()
    for i in range(1, n + 1):
        session.add(
            Frame(
                project_id=p.id,
                number=i,
                voiceover_text=f"реплика {i}",
                image_prompt=f"img {i}",
                animation_prompt=f"vid {i}",
                attrs={},
            )
        )
    await session.commit()
    return p


async def test_backfill_creates_scene_sort_uuid_texts_prompts_edges(session: AsyncSession):
    p = await _mk_project_with_frames(session, 3)
    stats = await db_v2.backfill_project_v2(session, p)
    await session.commit()

    assert stats["scenes"] == 1
    assert stats["uuids"] == 3
    assert stats["texts"] == 3
    assert stats["prompts"] == 6  # img + video на каждый кадр
    assert stats["edges"] == 2  # цепочка 1→2→3

    frames = list(
        (await session.execute(select(Frame).order_by(Frame.sort_key))).scalars()
    )
    assert [f.sort_key for f in frames] == [10.0, 20.0, 30.0]
    assert all(f.uuid for f in frames)
    assert all(f.scene_id == frames[0].scene_id for f in frames)

    # идемпотентность: повторный backfill ничего не добавляет
    stats2 = await db_v2.backfill_project_v2(session, p)
    assert stats2["texts"] == 0 and stats2["prompts"] == 0 and stats2["edges"] == 0


async def test_insert_frame_between_keeps_neighbors(session: AsyncSession):
    p = await _mk_project_with_frames(session, 2)
    await db_v2.backfill_project_v2(session, p)
    await session.commit()
    frames = list(
        (await session.execute(select(Frame).order_by(Frame.sort_key))).scalars()
    )
    keys_before = {f.id: f.sort_key for f in frames}

    new = await db_v2.insert_frame_after(session, p, after_frame_id=frames[0].id)
    await session.commit()

    assert new.sort_key == 15.0  # среднее между 10 и 20
    assert new.uuid
    # соседи не переименованы
    for fid, key in keys_before.items():
        fr = await session.get(Frame, fid)
        assert fr.sort_key == key

    # ниточка next перекинута: 1 → new → 2
    e1 = (
        await session.execute(
            select(FrameEdge).where(FrameEdge.from_frame_id == frames[0].id)
        )
    ).scalars().one()
    assert e1.to_frame_id == new.id
    e2 = (
        await session.execute(
            select(FrameEdge).where(FrameEdge.from_frame_id == new.id)
        )
    ).scalars().one()
    assert e2.to_frame_id == frames[1].id


async def test_insert_frame_at_start_and_end(session: AsyncSession):
    p = await _mk_project_with_frames(session, 1)
    await db_v2.backfill_project_v2(session, p)
    await session.commit()
    first = (
        await session.execute(select(Frame).order_by(Frame.sort_key))
    ).scalars().first()

    head = await db_v2.insert_frame_after(session, p, after_frame_id=None)
    assert head.sort_key < first.sort_key

    tail = await db_v2.insert_frame_after(session, p, after_frame_id=first.id)
    assert tail.sort_key > first.sort_key


async def test_prompt_versions_keep_history(session: AsyncSession):
    p = await _mk_project_with_frames(session, 1)
    await db_v2.backfill_project_v2(session, p)
    await session.commit()
    fr = (
        await session.execute(select(Frame).order_by(Frame.sort_key))
    ).scalars().first()

    pv2 = await db_v2.add_prompt_version(
        session, p.id, fr.id, kind="img", text="новый вариант"
    )
    await session.commit()
    assert pv2.version == 2 and pv2.is_active

    prompts = list(
        (
            await session.execute(
                select(PromptVersion).where(
                    PromptVersion.frame_id == fr.id, PromptVersion.kind == "img"
                )
            )
        ).scalars()
    )
    assert len(prompts) == 2
    assert sum(1 for x in prompts if x.is_active) == 1
    assert {x.version for x in prompts} == {1, 2}  # v1 не затёрта


async def test_project_graph_nested(session: AsyncSession):
    p = await _mk_project_with_frames(session, 2)
    await db_v2.backfill_project_v2(session, p)
    await session.commit()

    g = await db_v2.project_graph(session, p)
    assert g["project"]["slug"] == "v2test"
    assert len(g["scenes"]) == 1
    assert len(g["frames"]) == 2
    fr = g["frames"][0]
    assert fr["uuid"]
    assert fr["texts"][0]["kind"] == "voiceover"
    assert {pp["kind"] for pp in fr["prompts"]} == {"img", "video"}
    assert fr["edges"][0]["type"] == "next"


async def test_sort_key_between_edges(session: AsyncSession):
    assert db_v2.sort_key_between(None, None) == 10.0
    assert db_v2.sort_key_between(None, 10.0) == 0.0
    assert db_v2.sort_key_between(10.0, None) == 20.0
    assert db_v2.sort_key_between(10.0, 20.0) == 15.0
    _ = (FrameText, Scene)  # импорты используются в фикстурах выше
