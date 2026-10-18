"""R0b: короткий список кадров и новый реестр сцен не теряют старые id."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services import db_v2
from app.services.db_apply import apply_ops, upsert_scene_registry


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'keep.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_db_apply_partial_kadry_and_scene_registry_keep_missing(
    session: AsyncSession,
) -> None:
    project = Project(
        slug="keep-missing",
        topic="t",
        hero_mode="auto",
        meta={
            "scene_registry": [
                {
                    "id_scene": "scene_01",
                    "start_words": "утро",
                    "end_words": "день",
                },
                {
                    "id_scene": "scene_02",
                    "start_words": "вечер",
                    "end_words": "ночь",
                },
            ]
        },
    )
    session.add(project)
    await session.flush()
    frame = Frame(
        project_id=project.id,
        number=1,
        uuid="cd" * 12,
        voiceover_text="текст",
        sort_key=10.0,
        status="planned",
        attrs={
            "кадры": [
                {"id": "1-K1", "план": "ОБЩИЙ"},
                {"id": "1-K2", "план": "КРУПНЫЙ"},
            ]
        },
    )
    session.add(frame)
    await session.commit()

    await apply_ops(
        session,
        project,
        [
            {
                "frame_uuid": frame.uuid,
                "fields": {"кадры": [{"id": "1-K1", "план": "СРЕДНИЙ"}]},
            }
        ],
    )
    await session.commit()
    live = await session.get(Frame, frame.id)
    ladder = (live.attrs or {}).get("кадры") or []
    by_id = {row["id"]: row for row in ladder}
    assert by_id["1-K1"]["план"] == "СРЕДНИЙ"
    assert by_id["1-K2"]["активен"] is False
    assert [row["id"] for row in ladder] == ["1-K1", "1-K2"]

    upsert_scene_registry(
        project,
        [
            {
                "id_scene": "scene_01",
                "start_words": "утро",
                "end_words": "полдень",
            }
        ],
    )
    registry = project.meta["scene_registry"]
    ids = [row["id_scene"] for row in registry]
    assert ids == ["scene_01", "scene_02"]
    assert registry[1]["активен"] is False
    assert registry[0].get("активен") is not False
