"""WS1: apply-ops и replace_all_frames не теряют шоты и кадры."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services import db_v2
from app.services.db_apply import apply_ops
from app.services.frame_tombstones import deleted_numbers, frame_is_active, shot_is_active


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


@pytest.mark.asyncio
async def test_kadry_replace_keeps_missing_shots_inactive(session: AsyncSession) -> None:
    project = Project(slug="ws1-kadry", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    fr = Frame(
        project_id=project.id,
        number=7,
        uuid="kadry-keep-frame",
        voiceover_text="закадр ячейки",
        attrs={
            "кадры": [
                {"id": "7-K1", "порядок": 1, "закадр": "первый"},
                {"id": "7-K2", "порядок": 2, "закадр": "второй"},
                {"id": "7-K3", "порядок": 3, "закадр": "третий"},
            ]
        },
    )
    session.add(fr)
    await session.commit()

    await apply_ops(
        session,
        project,
        [
            {
                "frame_uuid": fr.uuid,
                "fields": {
                    "кадры": [
                        {"id": "7-K1", "порядок": 1, "закадр": "первый обновлён"},
                        {"id": "7-K4", "порядок": 4, "закадр": "новый шот"},
                    ]
                },
            }
        ],
        export_xlsx=False,
    )
    await session.refresh(fr)
    shots = list(fr.attrs["кадры"])
    by_id = {shot["id"]: shot for shot in shots}
    assert set(by_id) == {"7-K1", "7-K2", "7-K3", "7-K4"}
    assert len(shots) == 4
    assert by_id["7-K2"]["закадр"] == "второй"
    assert by_id["7-K2"]["активен"] is False
    assert shot_is_active(by_id["7-K2"]) is False
    assert by_id["7-K3"]["активен"] is False
    assert shot_is_active(by_id["7-K3"]) is False
    assert by_id["7-K1"]["закадр"] == "первый обновлён"
    assert shot_is_active(by_id["7-K1"]) is True
    assert shot_is_active(by_id["7-K4"]) is True
    assert fr.voiceover_text == "закадр ячейки"
