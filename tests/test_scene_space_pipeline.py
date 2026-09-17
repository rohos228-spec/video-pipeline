"""Live pipeline ingest into scene_space overlay."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sqlalchemy import select

from app.models import Base, Entity, Frame, Project, ProjectStatus, Scene
from app.services.scene_space.migrate import migrate_scene_space_schema
from app.services.scene_space.pipeline import (
    ingest_project,
    overlay_hash,
    parse_space_id,
    space_id_for,
)
from app.services.scene_space.store import list_frame_spaces


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await migrate_scene_space_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _seed(session: AsyncSession) -> tuple[Project, Scene]:
    project = Project(slug="space-live", topic="t", status=ProjectStatus.new)
    session.add(project)
    await session.flush()
    session.add(Entity(project_id=project.id, type="character", code="c01", name="A"))
    session.add(Entity(project_id=project.id, type="character", code="c02", name="B"))
    sc = Scene(
        project_id=project.id,
        sort_key=10.0,
        title="folder",
        meaning="A wins then loses",
        place="lobby",
    )
    session.add(sc)
    await session.flush()
    sizes = ("общий", "по пояс", "крупный", "деталь")
    for i, ru in enumerate(sizes, start=1):
        session.add(
            Frame(
                project_id=project.id,
                scene_id=sc.id,
                number=i,
                sort_key=float(i * 10),
                voiceover_text=f"vo {i}",
                uuid=f"{i:024x}",
                attrs={"крупность": ru, "accent": f"a{i}", "characters": "c01, c02"},
                image_prompt="PROMPT: lobby pair\nNEGATIVE PROMPT: extra limbs, watermark",
            )
        )
    await session.commit()
    return project, sc


def test_space_id_roundtrip():
    assert space_id_for(13, 4) == "p13:s4"
    assert parse_space_id("p13:s4") == (13, 4)
    assert parse_space_id("fix:dialogue") is None


async def test_ingest_overlay_maps_ru_sizes_and_is_idempotent(session: AsyncSession):
    project, sc = await _seed(session)
    first = await ingest_project(session, project)
    sid = space_id_for(project.id, sc.id)
    assert first["scenes"] == 1
    assert first["reports"][0]["scene_id"] == sid
    assert first["reports"][0]["skipped"] is False
    rows = await list_frame_spaces(session, sid)
    assert [r["shot_size"] for r in rows] == ["WS", "MS", "CU", "ECU"]
    assert all(r["axis_side"] in {"A", "B"} for r in rows)
    assert rows[0]["axis_pair"] == "c01|c02"
    frames_n = first["reports"][0]["frames"]
    second = await ingest_project(session, project)
    assert second["reports"][0]["skipped"] is True
    again = await list_frame_spaces(session, sid)
    assert len(again) == frames_n == 4


async def test_ingest_keeps_manual_row(session: AsyncSession):
    project, sc = await _seed(session)
    await ingest_project(session, project)
    sid = space_id_for(project.id, sc.id)
    from sqlalchemy import text

    await session.execute(
        text("UPDATE frames_space SET manual=1, shot_size='EWS' WHERE uuid=:u"),
        {"u": f"{1:024x}"},
    )
    await session.flush()
    from sqlalchemy import select

    fr = (
        await session.execute(select(Frame).where(Frame.uuid == f"{1:024x}"))
    ).scalar_one()
    fr.attrs = {**fr.attrs, "крупность": "в полный рост"}
    await session.flush()
    await ingest_project(session, project)
    rows = await list_frame_spaces(session, sid)
    kept = next(r for r in rows if r["uuid"] == f"{1:024x}")
    assert int(kept["manual"]) == 1
    assert kept["shot_size"] == "EWS"


async def test_overlay_hash_changes_with_size(session: AsyncSession):
    project, _sc = await _seed(session)
    from sqlalchemy import select

    frames = (
        await session.execute(select(Frame).where(Frame.project_id == project.id))
    ).scalars().all()
    a = overlay_hash("m", list(frames))
    frames[0].attrs = {**frames[0].attrs, "крупность": "деталь"}
    b = overlay_hash("m", list(frames))
    assert a != b
