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
    merge_montage_scenes,
    set_montage_voiceover,
    split_montage_scene_at,
    split_montage_scene_frames,
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


def _ordered(frames: list[Frame]) -> list[Frame]:
    return sorted(frames, key=lambda fr: (float(fr.sort_key or 0.0), int(fr.number or 0)))


@pytest.mark.asyncio
async def test_insert_between_keeps_neighbor_numbers(
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
    assert inserted.number == 3

    rows = _ordered(
        list(
            (
                await session.execute(
                    select(Frame).where(Frame.project_id == project.id)
                )
            ).scalars()
        )
    )
    assert [fr.voiceover_text for fr in rows] == ["первый", "между", "второй"]
    assert [fr.number for fr in rows] == [1, 3, 2]
    texts = (
        await session.execute(
            select(FrameText).where(
                FrameText.frame_id == inserted.id, FrameText.kind == "voiceover"
            )
        )
    ).scalars().all()
    assert len(texts) == 1 and texts[0].text == "между"


@pytest.mark.asyncio
async def test_insert_between_when_sort_key_missing(
    session: AsyncSession, project: Project
) -> None:
    """Старые кадры без sort_key не должны выталкивать вставку в конец."""
    session.add(project)
    a = _vo_parent(project.id, 1, "aa" * 12, "первый")
    b = _vo_parent(project.id, 2, "bb" * 12, "второй")
    a.sort_key = None
    b.sort_key = None
    session.add_all([a, b])
    await session.flush()

    inserted = await insert_montage_frame(
        session, project, after_frame_id=a.id, voiceover="между"
    )
    await session.commit()
    rows = _ordered(
        list(
            (
                await session.execute(
                    select(Frame).where(Frame.project_id == project.id)
                )
            ).scalars()
        )
    )
    assert [fr.voiceover_text for fr in rows] == ["первый", "между", "второй"]
    assert inserted.number == 3


@pytest.mark.asyncio
async def test_insert_child_stays_in_same_scene(
    session: AsyncSession, project: Project
) -> None:
    from app.services.vo_shot_expand import is_shot_child

    session.add(project)
    a = _vo_parent(project.id, 1, "aa" * 12, "ячейка")
    b = _vo_parent(project.id, 2, "bb" * 12, "дальше")
    session.add_all([a, b])
    await session.flush()

    kid = await insert_montage_frame(
        session, project, after_frame_id=a.id, kind="child"
    )
    await session.commit()
    rows = _ordered(
        list(
            (
                await session.execute(
                    select(Frame).where(Frame.project_id == project.id)
                )
            ).scalars()
        )
    )
    assert [fr.number for fr in rows] == [1, 3, 2]
    assert is_shot_child(kid)
    assert rows[1].id == kid.id
    assert rows[2].voiceover_text == "дальше"


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
async def test_delete_parent_keeps_children(
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
    assert result["deleted"] == 1
    assert result["promoted"] == 2
    left = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.number)
            )
        ).scalars()
    )
    assert [fr.number for fr in left] == [2, 3]
    assert left[0].voiceover_text == "кусок"
    assert left[1].voiceover_text == "дальше"


@pytest.mark.asyncio
async def test_insert_does_not_steal_neighbor_image(
    session: AsyncSession, project: Project
) -> None:
    """Новый кадр пустой: PNG соседа остаются на соседе, номер не сдвигается."""
    from app.services.montage_board import build_montage_board

    session.add(project)
    a = _vo_parent(project.id, 1, "aa" * 12, "первый")
    b = _vo_parent(project.id, 2, "bb" * 12, "второй")
    session.add_all([a, b])
    await session.flush()
    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    (scenes / "frame_001_a.png").write_bytes(b"png-a")
    (scenes / "frame_002_b.png").write_bytes(b"png-b")

    kid = await insert_montage_frame(
        session, project, after_frame_id=a.id, kind="child"
    )
    await session.commit()

    assert kid.number == 3
    live_b = await session.get(Frame, b.id)
    assert live_b is not None and live_b.number == 2

    board = await build_montage_board(session, project)
    ordered = board["frames"]
    assert [row["frame_id"] for row in ordered] == [a.id, kid.id, b.id]
    kid_row = ordered[1]
    b_row = ordered[2]
    assert not kid_row["image_shot1_url"]
    assert not (kid_row.get("image_prompt_shot1") or "").strip()
    assert b_row["image_shot1_url"]
    assert "frame_002" in (b_row["image_shot1_url"] or "")


@pytest.mark.asyncio
async def test_merge_scenes_keeps_numbers(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_board import build_montage_board
    from app.services.vo_shot_expand import is_shot_child

    session.add(project)
    a = _vo_parent(project.id, 13, "aa" * 12, "первая ячейка")
    b = _vo_parent(project.id, 14, "bb" * 12, "вторая ячейка")
    c = _vo_parent(project.id, 15, "cc" * 12, "хвост второй")
    c.attrs = {
        "camera_subdivide": {
            "role": "shot",
            "parent_uuid": "bb" * 12,
        }
    }
    session.add_all([a, b, c])
    await session.flush()

    result = await merge_montage_scenes(
        session, project, left_frame_id=int(a.id), right_frame_id=int(b.id)
    )
    await session.commit()
    assert result["parent_number"] == 13
    assert result["vo_scene_size"] == 3

    rows = _ordered(
        list(
            (
                await session.execute(
                    select(Frame).where(Frame.project_id == project.id)
                )
            ).scalars()
        )
    )
    assert [fr.number for fr in rows] == [13, 14, 15]
    assert is_shot_child(rows[1]) is True
    assert is_shot_child(rows[2]) is True

    board = await build_montage_board(session, project)
    by_n = {fr["number"]: fr for fr in board["frames"]}
    assert by_n[13]["vo_scene_number"] == 13
    assert by_n[14]["vo_scene_number"] == 13
    assert by_n[15]["vo_scene_number"] == 13
    assert by_n[13]["vo_scene_size"] == 3


@pytest.mark.asyncio
async def test_split_scene_at_keeps_numbers(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_board import build_montage_board
    from app.services.vo_shot_expand import is_shot_child

    session.add(project)
    a = _vo_parent(project.id, 21, "aa" * 12, "голова")
    b = Frame(
        project_id=project.id,
        number=22,
        uuid="bb" * 12,
        voiceover_text="середина",
        sort_key=220.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    c = Frame(
        project_id=project.id,
        number=23,
        uuid="cc" * 12,
        voiceover_text="хвост",
        sort_key=230.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    session.add_all([a, b, c])
    await session.flush()

    result = await split_montage_scene_at(
        session, project, at_frame_id=int(b.id)
    )
    await session.commit()
    assert result["left_number"] == 21
    assert result["right_number"] == 22
    assert result["left_size"] == 1
    assert result["right_size"] == 2

    rows = _ordered(
        list(
            (
                await session.execute(
                    select(Frame).where(Frame.project_id == project.id)
                )
            ).scalars()
        )
    )
    assert [fr.number for fr in rows] == [21, 22, 23]
    assert is_shot_child(rows[0]) is False
    assert is_shot_child(rows[1]) is False
    assert is_shot_child(rows[2]) is True

    board = await build_montage_board(session, project)
    by_n = {fr["number"]: fr for fr in board["frames"]}
    assert by_n[21]["vo_scene_number"] == 21
    assert by_n[21]["vo_scene_size"] == 1
    assert by_n[22]["vo_scene_number"] == 22
    assert by_n[23]["vo_scene_number"] == 22
    assert by_n[22]["vo_scene_size"] == 2


@pytest.mark.asyncio
async def test_split_scene_all_each_frame_own_scene(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_board import build_montage_board
    from app.services.vo_shot_expand import is_shot_child

    session.add(project)
    a = _vo_parent(project.id, 31, "aa" * 12, "один")
    b = Frame(
        project_id=project.id,
        number=32,
        uuid="bb" * 12,
        voiceover_text="два",
        sort_key=320.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    c = Frame(
        project_id=project.id,
        number=33,
        uuid="cc" * 12,
        voiceover_text="три",
        sort_key=330.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    session.add_all([a, b, c])
    await session.flush()

    result = await split_montage_scene_frames(
        session, project, frame_id=int(a.id)
    )
    await session.commit()
    assert result["scenes"] == 3

    rows = _ordered(
        list(
            (
                await session.execute(
                    select(Frame).where(Frame.project_id == project.id)
                )
            ).scalars()
        )
    )
    assert [fr.number for fr in rows] == [31, 32, 33]
    assert all(is_shot_child(fr) is False for fr in rows)

    board = await build_montage_board(session, project)
    by_n = {fr["number"]: fr for fr in board["frames"]}
    assert by_n[31]["vo_scene_number"] == 31
    assert by_n[32]["vo_scene_number"] == 32
    assert by_n[33]["vo_scene_number"] == 33
    assert by_n[31]["vo_scene_size"] == 1
    assert by_n[32]["vo_scene_size"] == 1
    assert by_n[33]["vo_scene_size"] == 1


@pytest.mark.asyncio
async def test_split_all_visible_ids_hides_extra_shots(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_board import build_montage_board
    from app.services.vo_shot_expand import _cs, is_shot_child

    session.add(project)
    a = _vo_parent(project.id, 41, "aa" * 12, "один")
    b = Frame(
        project_id=project.id,
        number=42,
        uuid="bb" * 12,
        voiceover_text="два",
        sort_key=420.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    c = Frame(
        project_id=project.id,
        number=43,
        uuid="cc" * 12,
        voiceover_text="три",
        sort_key=430.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    extra = Frame(
        project_id=project.id,
        number=44,
        uuid="dd" * 12,
        voiceover_text="хвост",
        sort_key=440.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
    )
    session.add_all([a, b, c, extra])
    await session.flush()

    result = await split_montage_scene_frames(
        session,
        project,
        frame_id=int(a.id),
        frame_ids=[int(a.id), int(b.id), int(c.id)],
    )
    await session.commit()
    assert result["scenes"] == 3

    rows = _ordered(
        list(
            (
                await session.execute(
                    select(Frame).where(Frame.project_id == project.id)
                )
            ).scalars()
        )
    )
    by_n = {int(fr.number): fr for fr in rows}
    assert is_shot_child(by_n[41]) is False
    assert is_shot_child(by_n[42]) is False
    assert is_shot_child(by_n[43]) is False
    assert is_shot_child(by_n[44]) is True
    assert bool(_cs(by_n[44]).get("leftover")) is True

    board = await build_montage_board(session, project)
    shown = {fr["number"]: fr for fr in board["frames"]}
    assert shown[44]["shot_leftover"] is True
    assert shown[41]["vo_scene_size"] == 2
    assert shown[42]["vo_scene_number"] == 42
    assert shown[43]["vo_scene_number"] == 43


@pytest.mark.asyncio
async def test_delete_does_not_resurrect_from_disk_png(
    session: AsyncSession, project: Project
) -> None:
    from app.services.ensure_frames_from_disk import ensure_frames_from_disk_media
    from app.services.montage_board import build_montage_board

    session.add(project)
    fr = _vo_parent(project.id, 19, "aa" * 12, "лишний")
    other = _vo_parent(project.id, 20, "bb" * 12, "сосед")
    session.add_all([fr, other])
    await session.flush()
    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    png = scenes / "frame_019_ghost.png"
    png.write_bytes(b"png-19")

    await delete_montage_frame(session, project, int(fr.id))
    await session.commit()
    assert not png.exists()
    assert await ensure_frames_from_disk_media(session, project) == []
    board = await build_montage_board(session, project)
    assert [row["number"] for row in board["frames"]] == [20]


@pytest.mark.asyncio
async def test_delete_other_frame_keeps_pending_ops(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_board_meta import montage_meta

    project.meta = {
        "montage_board": {
            "pending_ops": [
                {
                    "type": "coverage_action",
                    "frame_number": 1,
                    "shot": 1,
                    "action": "оставить",
                },
                {
                    "type": "image_ai_change",
                    "frame_number": 2,
                    "shot": 1,
                    "instruction": "убрать",
                },
            ]
        }
    }
    session.add(project)
    keep = _vo_parent(project.id, 1, "aa" * 12, "правлю")
    drop = _vo_parent(project.id, 2, "bb" * 12, "удаляю")
    session.add_all([keep, drop])
    await session.flush()

    await delete_montage_frame(session, project, int(drop.id))
    ops = montage_meta(project).get("pending_ops") or []
    assert [op["frame_number"] for op in ops] == [1]
    assert ops[0]["action"] == "оставить"

def _three_frame_scene(project_id: int) -> tuple[Frame, Frame, Frame]:
    a = _vo_parent(project_id, 21, "aa" * 12, "голова")
    kids = []
    for num, uid, vo in ((22, "bb" * 12, "середина"), (23, "cc" * 12, "хвост")):
        kids.append(
            Frame(
                project_id=project_id,
                number=num,
                uuid=uid,
                voiceover_text=vo,
                sort_key=float(num * 10),
                status="planned",
                attrs={"camera_subdivide": {"role": "shot", "parent_uuid": "aa" * 12}},
            )
        )
    return a, kids[0], kids[1]


async def _attrs_by_number(session: AsyncSession, project_id: int) -> dict[int, dict]:
    from copy import deepcopy

    rows = (await session.execute(select(Frame).where(Frame.project_id == project_id))).scalars().all()
    return {int(fr.number): deepcopy(dict(fr.attrs or {})) for fr in rows}


@pytest.mark.asyncio
async def test_split_undo_restores_scenes_and_keeps_paid_media(
    session: AsyncSession, project: Project
) -> None:
    from app.models import Artifact, ArtifactKind
    from app.services.montage_board_frames import split_undo_info, undo_montage_split

    session.add(project)
    a, b, c = _three_frame_scene(project.id)
    session.add_all([a, b, c])
    await session.flush()
    session.add_all(
        [
            Artifact(project_id=project.id, frame_id=b.id, kind=ArtifactKind.scene_image,
                     uuid="img-b", path="x/b.png"),
            Artifact(project_id=project.id, frame_id=c.id, kind=ArtifactKind.scene_video,
                     uuid="vid-c", path="x/c.mp4"),
        ]
    )
    await session.commit()
    before = await _attrs_by_number(session, project.id)

    res = await split_montage_scene_at(session, project, at_frame_id=int(b.id))
    await session.commit()
    assert res["undo_id"]
    assert (res["left_ordinal"], res["right_ordinal"]) == (1, 2)
    assert await _attrs_by_number(session, project.id) != before
    assert split_undo_info(project)["can_undo"] is True

    out = await undo_montage_split(session, project, undo_id=res["undo_id"])
    await session.commit()
    assert out["ok"] and out["paid_media_kept"] == {"images": 1, "videos": 1, "total": 2}
    assert await _attrs_by_number(session, project.id) == before
    assert split_undo_info(project)["can_undo"] is False
    arts = (await session.execute(select(Artifact).where(Artifact.project_id == project.id))).scalars().all()
    assert {(x.uuid, x.frame_id) for x in arts} == {("img-b", b.id), ("vid-c", c.id)}
    with pytest.raises(RuntimeError):
        await undo_montage_split(session, project)


@pytest.mark.asyncio
async def test_split_undo_refuses_when_frames_were_edited_after_split(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_board_frames import undo_montage_split

    session.add(project)
    a, b, c = _three_frame_scene(project.id)
    session.add_all([a, b, c])
    await session.flush()
    res = await split_montage_scene_at(session, project, at_frame_id=int(b.id))
    await session.commit()
    c.attrs = {**dict(c.attrs or {}), "camera_subdivide": {"role": "shot", "parent_uuid": "bb" * 12, "plan": "крупный"}}
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(c, "attrs")
    await session.commit()
    snapshot = await _attrs_by_number(session, project.id)
    with pytest.raises(RuntimeError, match="правили"):
        await undo_montage_split(session, project, undo_id=res["undo_id"])
    assert await _attrs_by_number(session, project.id) == snapshot


@pytest.mark.asyncio
async def test_split_undo_only_latest_and_split_all_is_undoable(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_board_frames import undo_montage_split

    session.add(project)
    a, b, c = _three_frame_scene(project.id)
    session.add_all([a, b, c])
    await session.flush()
    await session.commit()
    before = await _attrs_by_number(session, project.id)
    r = await split_montage_scene_frames(session, project, frame_id=int(a.id))
    await session.commit()
    assert r["undo_id"] and r["parent_ordinal"] == 1
    with pytest.raises(RuntimeError, match="последнее"):
        await undo_montage_split(session, project, undo_id="not-the-latest")
    await undo_montage_split(session, project, undo_id=r["undo_id"])
    await session.commit()
    assert await _attrs_by_number(session, project.id) == before


@pytest.mark.asyncio
async def test_board_shows_scene_ordinal_field(session: AsyncSession, project: Project) -> None:
    from app.services.montage_board import build_montage_board

    session.add(project)
    session.add_all(
        [
            _vo_parent(project.id, 5, "a5" * 12, "пять"),
            _vo_parent(project.id, 6, "a6" * 12, "шесть"),
            _vo_parent(project.id, 41, "a9" * 12, "сорок один"),
        ]
    )
    await session.flush()
    board = await build_montage_board(session, project)
    by_n = {fr["number"]: fr for fr in board["frames"]}
    keys = [by_n[n]["vo_scene_number"] for n in (5, 6, 41)]
    assert keys[-1] == 41
    distinct = list(dict.fromkeys(keys))
    assert [by_n[n]["vo_scene_ordinal"] for n in (5, 6, 41)] == [distinct.index(k) + 1 for k in keys]
    assert by_n[41]["vo_scene_ordinal"] == len(distinct)


def test_scene_display_ordinals_follow_timeline_not_historical_number() -> None:
    from app.services.montage_scene_editor import scene_display_ordinals

    f5 = _vo_parent(1, 5, "a5" * 12, "пять")
    f6 = _vo_parent(1, 6, "a6" * 12, "шесть")
    f41 = _vo_parent(1, 41, "a9" * 12, "вставка")
    f41.sort_key = 55.0
    for i, fr in enumerate((f5, f6, f41), start=1):
        fr.id = i
    got = scene_display_ordinals([f6, f41, f5])
    assert (got[f5.id], got[f41.id], got[f6.id]) == (1, 2, 3)


def test_busy_project_refuses_scene_edits() -> None:
    from fastapi import HTTPException

    from app.models import ProjectStatus
    from app.web.routers.project_ops import _refuse_if_project_busy

    idle = Project(id=1, slug="x", topic="t", status=ProjectStatus.new)
    _refuse_if_project_busy(idle)
    busy = Project(id=2, slug="y", topic="t", status=ProjectStatus.generating_images)
    with pytest.raises(HTTPException) as e:
        _refuse_if_project_busy(busy)
    assert e.value.status_code == 409
