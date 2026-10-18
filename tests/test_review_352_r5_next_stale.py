"""Ревью #352: устаревшее next помечается next_stale, не удаляется."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameEdge, Project
from app.services import db_v2
from app.services.vo_shot_expand import rebuild_vo_cells_from_shots


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
async def test_each_frame_has_one_live_next_and_stale_edge_stays(
    session: AsyncSession,
) -> None:
    project = Project(slug="next-stale", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    frames = []
    for number, text in ((1, "Первый кусок."), (2, "Второй кусок."), (3, "Третий кусок.")):
        frames.append(
            Frame(
                project_id=project.id,
                number=number,
                sort_key=float(number),
                uuid=f"next-{number}",
                voiceover_text=text,
                attrs={},
            )
        )
    session.add_all(frames)
    await session.flush()
    stale = FrameEdge(
        project_id=project.id,
        from_frame_id=frames[0].id,
        to_frame_id=frames[2].id,
        type="next",
    )
    session.add(stale)
    await session.commit()

    await rebuild_vo_cells_from_shots(session, project, frames)
    await session.commit()

    edges = list(
        (
            await session.execute(
                select(FrameEdge).where(FrameEdge.project_id == project.id)
            )
        ).scalars()
    )
    assert any(edge.id == stale.id and edge.type == "next_stale" for edge in edges)
    live = [edge for edge in edges if edge.type == "next"]
    by_from: dict[int, list[FrameEdge]] = {}
    for edge in live:
        by_from.setdefault(int(edge.from_frame_id), []).append(edge)
    ordered = sorted(frames, key=lambda fr: fr.sort_key)
    for fr, nxt in zip(ordered, ordered[1:]):
        assert len(by_from.get(int(fr.id), [])) == 1
        assert by_from[int(fr.id)][0].to_frame_id == nxt.id
    assert by_from.get(int(ordered[-1].id), []) == []
