"""Вставка / удаление кадров и правка закадра в монтаже."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameText, Project
from app.services import db_v2
from app.services.montage_board_frames import (
    delete_montage_frame,
    insert_montage_frame,
    set_montage_voiceover,
)


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mf.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    return Project(id=71, slug="montage-frames", topic="t", hero_mode="auto")


def _vo_parent(project_id: int, number: int, uid: str, vo: str) -> Frame:
    return Frame(
        project_id=project_id,
        number=number,
        uuid=uid,
        voiceover_text=vo,
        sort_key=float(number * 10),
        status="planned",
        attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": uid}},
    )


@pytest.mark.asyncio
async def test_insert_between_renumbers(
    session: AsyncSession, project: Project
) -> None:
    session.add(project)
    a = _vo_parent(project.id, 1, "aa" * 12, "первый")
    b = _vo_parent(project.id, 2, "bb" * 12, "второй")
    session.add_all([a, b])
    await session.flush()

    inserted = await insert_montage_frame(
        session, project, after_frame_id=a.id, voiceover="между"
    )
    await session.commit()
    assert (inserted.voiceover_text or "") == "между"

    rows = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.number)
            )
        ).scalars()
    )
    assert [fr.voiceover_text for fr in rows] == ["первый", "между", "второй"]
    assert [fr.number for fr in rows] == [1, 2, 3]
    texts = (
        await session.execute(
            select(FrameText).where(
                FrameText.frame_id == inserted.id, FrameText.kind == "voiceover"
            )
        )
    ).scalars().all()
    assert len(texts) == 1 and texts[0].text == "между"


@pytest.mark.asyncio
async def test_edit_and_clear_voiceover(
    session: AsyncSession, project: Project
) -> None:
    session.add(project)
    fr = _vo_parent(project.id, 1, "cc" * 12, "было")
    session.add(fr)
    await session.flush()

    await set_montage_voiceover(session, project, fr.id, "стало")
    await session.commit()
    live = await session.get(Frame, fr.id)
    assert live is not None and live.voiceover_text == "стало"

    await set_montage_voiceover(session, project, fr.id, "  ")
    await session.commit()
    live = await session.get(Frame, fr.id)
    assert live is not None and live.voiceover_text == ""
    left = (
        await session.execute(
            select(FrameText).where(
                FrameText.frame_id == fr.id, FrameText.kind == "voiceover"
            )
        )
    ).scalars().all()
    assert left == []


@pytest.mark.asyncio
async def test_delete_parent_drops_children(
    session: AsyncSession, project: Project
) -> None:
    session.add(project)
    parent_uid = "dd" * 12
    parent = _vo_parent(project.id, 1, parent_uid, "ячейка")
    child = Frame(
        project_id=project.id,
        number=2,
        uuid="ee" * 12,
        voiceover_text="кусок",
        sort_key=15.0,
        status="planned",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "coverage_parent_id": "1-K1",
            }
        },
    )
    other = _vo_parent(project.id, 3, "ff" * 12, "дальше")
    session.add_all([parent, child, other])
    await session.flush()
    parent_id = parent.id

    result = await delete_montage_frame(session, project, parent_id)
    await session.commit()
    assert result["deleted"] == 2
    left = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.number)
            )
        ).scalars()
    )
    assert len(left) == 1
    assert left[0].voiceover_text == "дальше"
    assert left[0].number == 1
