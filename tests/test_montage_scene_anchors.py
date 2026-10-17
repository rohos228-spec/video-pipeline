"""Якоря сцены на доске = все якоря, чей текст лежит в закадре этой сцены."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services import db_v2
from app.services.montage_board import build_montage_board
from app.services.montage_board_frames import merge_montage_scenes, split_montage_scene_at
from app.services.montage_coverage_ops import apply_coverage_anchors
from app.services.vo_shot_expand import bits_from_attrs

A_TEXT = "Тед Банди родился в Берлингтоне. Детство прошло у деда."
B_TEXT = "Позже он переехал в Сиэтл и поступил в университет."


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'anchors.db'}")
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
    return Project(id=81, slug="anchors", topic="t", hero_mode="auto")


def _frame(project: Project, number: int, uid: str, vo: str, *, parent: str = "", bits=None) -> Frame:
    cs = {"role": "shot", "parent_uuid": parent} if parent else {"role": "vo_parent", "parent_uuid": uid}
    attrs: dict = {"camera_subdivide": cs}
    if bits is not None:
        attrs["биты"] = bits
    return Frame(
        project_id=project.id,
        number=number,
        uuid=uid,
        voiceover_text=vo,
        sort_key=float(number * 10),
        status="planned",
        attrs=attrs,
    )


def _bit(n: int, anchor: str, change: str = "") -> dict:
    return {"порядок": n, "якорь": anchor, "изменение": change}


async def _anchors(session: AsyncSession, project: Project) -> dict[int, list[str]]:
    board = await build_montage_board(session, project)
    return {
        fr["number"]: [r["якорь"] for r in fr.get("scene_anchor_rows") or []]
        for fr in board["frames"]
    }


async def _frames(session: AsyncSession, project: Project) -> dict[int, Frame]:
    rows = (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars()
    return {int(fr.number): fr for fr in rows}


@pytest.mark.asyncio
async def test_scene_shows_anchors_held_by_its_shots(session: AsyncSession, project: Project) -> None:
    session.add(project)
    head = _frame(project, 1, "aa" * 12, A_TEXT, bits=[_bit(1, "Тед Банди родился")])
    shot = _frame(project, 2, "bb" * 12, "", parent="aa" * 12, bits=[_bit(1, "Детство прошло у деда")])
    head.attrs["vo_cell_full"] = A_TEXT
    session.add_all([head, shot])
    await session.flush()

    anchors = await _anchors(session, project)
    assert anchors[1] == ["Тед Банди родился", "Детство прошло у деда"]
    assert anchors[2] == anchors[1]


@pytest.mark.asyncio
async def test_merge_moves_right_anchors_into_merged_scene(
    session: AsyncSession, project: Project
) -> None:
    session.add(project)
    a = _frame(project, 1, "aa" * 12, A_TEXT, bits=[_bit(1, "Тед Банди родился")])
    b = _frame(project, 2, "bb" * 12, B_TEXT, bits=[_bit(1, "Позже он переехал")])
    session.add_all([a, b])
    await session.flush()

    await merge_montage_scenes(session, project, left_frame_id=int(a.id), right_frame_id=int(b.id))
    await session.flush()

    anchors = await _anchors(session, project)
    assert anchors[1] == ["Тед Банди родился", "Позже он переехал"]
    frames = await _frames(session, project)
    assert [x["якорь"] for x in bits_from_attrs(frames[1])] == anchors[1]
    assert bits_from_attrs(frames[2]) == []


@pytest.mark.asyncio
async def test_split_sends_each_anchor_to_scene_with_its_text(
    session: AsyncSession, project: Project
) -> None:
    session.add(project)
    head = _frame(
        project,
        1,
        "aa" * 12,
        A_TEXT,
        bits=[_bit(1, "Тед Банди родился"), _bit(2, "Позже он переехал")],
    )
    tail = _frame(project, 2, "bb" * 12, B_TEXT, parent="aa" * 12)
    session.add_all([head, tail])
    await session.flush()

    await split_montage_scene_at(session, project, at_frame_id=int(tail.id))
    await session.flush()

    anchors = await _anchors(session, project)
    assert anchors[1] == ["Тед Банди родился"]
    assert anchors[2] == ["Позже он переехал"]
    frames = await _frames(session, project)
    assert [x["якорь"] for x in bits_from_attrs(frames[2])] == ["Позже он переехал"]


@pytest.mark.asyncio
async def test_legacy_orphan_anchor_is_shown_in_scene_with_its_text(
    session: AsyncSession, project: Project
) -> None:
    """Старые данные: после разделения якорь второй сцены остался на первой."""
    session.add(project)
    a = _frame(
        project,
        1,
        "aa" * 12,
        A_TEXT,
        bits=[_bit(1, "Тед Банди родился"), _bit(2, "Позже он переехал")],
    )
    b = _frame(project, 2, "bb" * 12, B_TEXT)
    a.attrs["place"] = "Берлингтон"
    b.attrs["place"] = "Сиэтл"
    session.add_all([a, b])
    await session.flush()

    anchors = await _anchors(session, project)
    assert anchors[1] == ["Тед Банди родился"]
    assert anchors[2] == ["Позже он переехал"]


@pytest.mark.asyncio
async def test_saved_anchor_list_is_final_deleted_anchor_does_not_return(
    session: AsyncSession, project: Project
) -> None:
    session.add(project)
    head = _frame(project, 1, "aa" * 12, A_TEXT, bits=[_bit(1, "Тед Банди родился")])
    shot = _frame(project, 2, "bb" * 12, "", parent="aa" * 12, bits=[_bit(1, "Детство прошло у деда")])
    head.attrs["vo_cell_full"] = A_TEXT
    session.add_all([head, shot])
    await session.flush()

    frames = list((await _frames(session, project)).values())
    await apply_coverage_anchors(
        session, project, head, frames, [{"якорь": "Тед Банди родился"}]
    )
    await session.flush()

    anchors = await _anchors(session, project)
    assert anchors[1] == ["Тед Банди родился"]
    stored = await _frames(session, project)
    assert bits_from_attrs(stored[2]) == []
