"""Правки покрытия (план / действие / родитель-дочерний) с панели монтажа."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Entity, Frame, Project
from app.services.montage_board import build_montage_board
from app.services.montage_board_apply import order_montage_pending_ops
from app.services.montage_board_meta import normalize_queue_ops, slot_key_from_op
from app.services.montage_board_regen import _montage_shot1_refs
from app.services.montage_coverage_ops import (
    COVERAGE_OP_TYPES,
    SCENE_FIELD_OP_TYPES,
    SCENE_FIELD_SPECS,
    apply_coverage_op,
)
from app.services.montage_scene_editor import frame_board_scene_cell
from app.services.vo_shot_expand import _cs, find_coverage_parent_frame, is_shot_child


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cov.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    return Project(id=40, slug="cov-edit", topic="t", hero_mode="auto")


def _parent_child(project_id: int) -> tuple[Frame, Frame]:
    parent_uid = "aa" * 12
    child_uid = "bb" * 12
    parent = Frame(
        project_id=project_id,
        number=1,
        uuid=parent_uid,
        voiceover_text="vo",
        status="planned",
        attrs={
            "shot01_action": "сидит",
            "кадры": [
                {"id": "1-K1", "план": "ОБЩИЙ", "действие": "сидит", "parent_id": None},
                {"id": "1-K2", "план": "ДЕТАЛЬ", "действие": "рука", "parent_id": "1-K1"},
            ],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "план": "ОБЩИЙ",
            },
        },
    )
    child = Frame(
        project_id=project_id,
        number=2,
        uuid=child_uid,
        voiceover_text="кусок",
        status="planned",
        attrs={
            "shot01_action": "рука",
            "кадры": [
                {"id": "1-K2", "план": "ДЕТАЛЬ", "действие": "рука", "parent_id": "1-K1"},
            ],
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "coverage_parent_id": "1-K1",
                "план": "ДЕТАЛЬ",
            },
        },
    )
    return parent, child


def test_normalize_keeps_parent_kind_without_parent_number() -> None:
    cleaned = normalize_queue_ops(
        [{"type": "coverage_kind", "frame_number": 2, "shot": 1, "kind": "parent"}]
    )
    assert cleaned == [
        {"type": "coverage_kind", "frame_number": 2, "shot": 1, "kind": "parent"}
    ]


def test_coverage_slot_keys_and_order() -> None:
    assert slot_key_from_op({"type": "coverage_plan", "frame_number": 7}) == "7:plan"
    assert slot_key_from_op({"type": "coverage_action", "frame_number": 7}) == "7:action"
    assert slot_key_from_op({"type": "coverage_angle", "frame_number": 7}) == "7:angle"
    assert slot_key_from_op({"type": "coverage_move", "frame_number": 7}) == "7:move"
    assert slot_key_from_op({"type": "coverage_stitch", "frame_number": 7}) == "7:stitch"
    assert slot_key_from_op({"type": "coverage_light", "frame_number": 7}) == "7:light"
    assert slot_key_from_op({"type": "coverage_set", "frame_number": 7}) == "7:set"
    assert slot_key_from_op({"type": "coverage_sense", "frame_number": 7}) == "7:sense"
    assert (
        slot_key_from_op({"type": "coverage_scene_action", "frame_number": 7})
        == "7:scene_action"
    )
    assert slot_key_from_op({"type": "coverage_place", "frame_number": 7}) == "7:place"
    assert slot_key_from_op({"type": "coverage_kind", "frame_number": 7}) == "7:kind"
    assert SCENE_FIELD_OP_TYPES <= COVERAGE_OP_TYPES
    assert slot_key_from_op({"type": "coverage_delete", "frame_number": 7}) == "7:kind"
    ordered = order_montage_pending_ops(
        [
            {"type": "image_regen", "frame_number": 2, "shot": 1},
            {"type": "coverage_plan", "frame_number": 2, "plan": "КРУПНЫЙ"},
            {"type": "video_regen", "frame_number": 2, "shot": 1},
        ]
    )
    assert [o["type"] for o in ordered] == [
        "coverage_plan",
        "image_regen",
        "video_regen",
    ]


@pytest.mark.asyncio
async def test_apply_plan_and_action(session: AsyncSession, project: Project) -> None:
    parent, child = _parent_child(project.id)
    session.add_all([project, parent, child])
    await session.flush()

    result = await apply_coverage_op(
        session,
        project,
        {"type": "coverage_plan", "frame_number": 2, "plan": "КРУПНЫЙ"},
    )
    assert result["ok"] is True
    assert result["highlight"] == "2:plan"
    await session.refresh(child)
    assert (child.attrs or {}).get("крупность") == "КРУПНЫЙ"
    assert (child.attrs or {}).get("camera_subdivide", {}).get("план") == "КРУПНЫЙ"

    result = await apply_coverage_op(
        session,
        project,
        {"type": "coverage_action", "frame_number": 2, "action": "берёт ключ"},
    )
    assert result["highlight"] == "2:action"
    await session.refresh(child)
    assert (child.attrs or {}).get("shot01_action") == "берёт ключ"


@pytest.mark.asyncio
async def test_apply_angle_move_stitch_and_scene_light(
    session: AsyncSession, project: Project
) -> None:
    parent, child = _parent_child(project.id)
    stranger_uid = "cc" * 12
    stranger = Frame(
        project_id=project.id,
        number=39,
        uuid=stranger_uid,
        voiceover_text="другая ячейка",
        status="planned",
        attrs={
            "освещение": "дневной",
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": stranger_uid,
                "shot_id": "1-S13-K1",
                "coverage_parent_id": "1-K1",
            },
        },
    )
    session.add_all([project, parent, child, stranger])
    await session.flush()

    result = await apply_coverage_op(
        session,
        project,
        {"type": "coverage_angle", "frame_number": 2, "angle": "3/4"},
    )
    assert result["highlight"] == "2:angle"
    assert result["regen_image"] is True
    await session.refresh(child)
    assert (child.attrs or {}).get("camera_subdivide", {}).get("ракурс") == "3/4"

    result = await apply_coverage_op(
        session,
        project,
        {"type": "coverage_move", "frame_number": 2, "move": "наезд"},
    )
    assert result["highlight"] == "2:move"
    await session.refresh(child)
    assert (child.attrs or {}).get("camera_subdivide", {}).get("движение") == "наезд"

    result = await apply_coverage_op(
        session,
        project,
        {"type": "coverage_stitch", "frame_number": 2, "stitch": "по действию"},
    )
    assert result["highlight"] == "2:stitch"
    assert result["regen_image"] is False
    await session.refresh(child)
    assert (child.attrs or {}).get("camera_subdivide", {}).get("переход") == "cut_on_action"

    result = await apply_coverage_op(
        session,
        project,
        {"type": "coverage_light", "frame_number": 2, "light": "ночной"},
    )
    assert result["highlight"] == "2:light"
    await session.refresh(parent)
    await session.refresh(child)
    await session.refresh(stranger)
    assert (parent.attrs or {}).get("освещение") == "ночной"
    assert (child.attrs or {}).get("освещение") == "ночной"
    assert (stranger.attrs or {}).get("освещение") == "дневной"

    result = await apply_coverage_op(
        session,
        project,
        {"type": "coverage_set", "frame_number": 2, "set": "кабинет ночью"},
    )
    assert result["highlight"] == "2:set"
    await session.refresh(parent)
    await session.refresh(stranger)
    assert (parent.attrs or {}).get("camera_subdivide", {}).get("набор") == "кабинет ночью"
    assert (stranger.attrs or {}).get("camera_subdivide", {}).get("набор") != "кабинет ночью"


@pytest.mark.asyncio
async def test_apply_scene_fields_write_board_keys(
    session: AsyncSession, project: Project
) -> None:
    """«Применить правки» пишет те attrs, которые читает клетка сцены."""
    parent, child = _parent_child(project.id)
    stranger_uid = "cc" * 12
    stranger = Frame(
        project_id=project.id,
        number=39,
        uuid=stranger_uid,
        voiceover_text="другая ячейка",
        status="planned",
        attrs={
            "смысл_сцены": "чужой смысл",
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": stranger_uid,
            },
        },
    )
    session.add_all([project, parent, child, stranger])
    await session.flush()

    values = {
        "coverage_sense": ("sense", "мужчина пишет пером"),
        "coverage_visual_type": ("visual_type", "Кинематографический реализм"),
        "coverage_place": ("place", "кабинет у окна"),
        "coverage_characters": ("characters", "c01"),
        "coverage_props": ("props", "перо, чернильница"),
        "coverage_bg": ("bg", "тёмные панели"),
        "coverage_accent": ("accent", "перо на бумаге"),
        "coverage_feature": ("feature", "средний план у окна"),
    }
    for op_type, (key, text) in values.items():
        result = await apply_coverage_op(
            session, project, {"type": op_type, "frame_number": 2, key: text}
        )
        spec = next(s for s in SCENE_FIELD_SPECS if s.op_type == op_type)
        assert result["highlight"] == f"2:{spec.slot}"
        assert result["regen_image"] is True

    await session.refresh(parent)
    await session.refresh(child)
    await session.refresh(stranger)
    cell = frame_board_scene_cell([parent, child, stranger], parent)
    assert cell["scene_sense"] == ""
    assert cell["scene_visual_type"] == ""
    assert cell["scene_place"] == ""
    assert cell["scene_characters"] == "c01"
    assert cell["scene_props"] == ""
    assert cell["scene_bg"] == ""
    assert cell["scene_accent"] == ""
    assert cell["scene_feature"] == ""
    assert (parent.attrs or {}).get("смысл_сцены") == "мужчина пишет пером"
    assert (child.attrs or {}).get("смысл_сцены") == "мужчина пишет пером"
    assert (parent.attrs or {}).get("тип_сцены") == "Кинематографический реализм"
    assert (parent.attrs or {}).get("visual_type") == "Кинематографический реализм"
    assert (parent.attrs or {}).get("camera_subdivide", {}).get("место") == "кабинет у окна"
    assert (child.attrs or {}).get("место") == "кабинет у окна"
    assert (parent.attrs or {}).get("characters") == "c01"
    assert (parent.attrs or {}).get("shot01_props") == "перо, чернильница"
    assert (parent.attrs or {}).get("accent") == "перо на бумаге"
    assert (child.attrs or {}).get("shot01_props") == "перо, чернильница"
    assert (stranger.attrs or {}).get("смысл_сцены") == "чужой смысл"

    queued = normalize_queue_ops(
        [
            {
                "type": "coverage_sense",
                "frame_number": 1,
                "sense": "новый смысл",
            },
            {
                "type": "coverage_scene_action",
                "frame_number": 5,
                "action": "входит и берёт папку",
            },
        ]
    )
    assert queued[-1]["type"] == "coverage_scene_action"
    assert queued[-1]["action"] == "входит и берёт папку"
    assert queued[0] == {
        "type": "coverage_sense",
        "frame_number": 1,
        "shot": 1,
        "sense": "новый смысл",
    }


@pytest.mark.asyncio
async def test_apply_scene_field_rejects_empty(
    session: AsyncSession, project: Project
) -> None:
    parent, child = _parent_child(project.id)
    session.add_all([project, parent, child])
    await session.flush()
    with pytest.raises(RuntimeError, match="смысл сцены пустой"):
        await apply_coverage_op(
            session, project, {"type": "coverage_sense", "frame_number": 1, "sense": "  "}
        )


@pytest.mark.asyncio
async def test_relink_and_delete_child(session: AsyncSession, project: Project) -> None:
    parent, child = _parent_child(project.id)
    other_uid = "cc" * 12
    other = Frame(
        project_id=project.id,
        number=3,
        uuid=other_uid,
        voiceover_text="другой",
        status="planned",
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": other_uid,
                "shot_id": "3-K1",
            }
        },
    )
    session.add_all([project, parent, child, other])
    await session.flush()

    await apply_coverage_op(
        session,
        project,
        {
            "type": "coverage_kind",
            "frame_number": 2,
            "kind": "child",
            "parent_number": 3,
        },
    )
    rows = list(
        (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        )
        .scalars()
        .all()
    )
    child2 = next(fr for fr in rows if int(fr.number) == 2)
    found = find_coverage_parent_frame(rows, child2)
    assert found is not None
    assert int(found.number) == 3
    assert _cs(child2).get("coverage_parent_number") == 3
    assert is_shot_child(child2) is True
    board = await build_montage_board(session, project)
    c_row = next(fr for fr in board["frames"] if fr["number"] == 2)
    assert c_row["vo_scene_number"] == 1
    assert c_row["shot_parent_number"] == 3

    await apply_coverage_op(
        session,
        project,
        {"type": "coverage_delete", "frame_number": 2},
    )
    gone = await session.get(Frame, child.id)
    assert gone is None


@pytest.mark.asyncio
async def test_vo_parent_can_become_child_of_its_shot(
    session: AsyncSession, project: Project
) -> None:
    """Первый кадр сцены можно сделать дочерним — цикл still разрывается."""
    from app.services.vo_shot_expand import _cs

    parent, child = _parent_child(project.id)
    session.add_all([project, parent, child])
    await session.flush()

    await apply_coverage_op(
        session,
        project,
        {
            "type": "coverage_kind",
            "frame_number": 1,
            "kind": "child",
            "parent_number": 2,
        },
    )
    rows = list(
        (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        )
        .scalars()
        .all()
    )
    head = next(fr for fr in rows if int(fr.number) == 1)
    kid = next(fr for fr in rows if int(fr.number) == 2)
    assert _cs(head).get("coverage_kind") == "child"
    assert _cs(kid).get("coverage_kind") == "parent"
    found = find_coverage_parent_frame(rows, head)
    assert found is not None
    assert int(found.number) == 2
    board = await build_montage_board(session, project)
    h_row = next(fr for fr in board["frames"] if fr["number"] == 1)
    k_row = next(fr for fr in board["frames"] if fr["number"] == 2)
    assert h_row["shot_kind"] == "child"
    assert h_row["shot_parent_number"] == 2
    assert k_row["shot_kind"] == "parent"


@pytest.mark.asyncio
async def test_promote_child_to_parent_drops_parent_ref(
    session: AsyncSession, project: Project
) -> None:
    """child→parent: still родителя с кадра снимается и не идёт в генерацию."""
    parent, child = _parent_child(project.id)
    parent_attrs = dict(parent.attrs or {})
    parent_attrs["characters"] = "c02"
    parent_attrs["персонажи"] = "c02"
    parent.attrs = parent_attrs
    data = project.data_dir
    scenes = data / "scenes"
    chars = data / "characters"
    scenes.mkdir(parents=True, exist_ok=True)
    chars.mkdir(parents=True, exist_ok=True)
    parent_png = scenes / "frame_001_shot1.png"
    parent_png.write_bytes(b"png-parent-still")
    (chars / "c02.png").write_bytes(b"png-c02")
    session.add_all(
        [
            project,
            parent,
            child,
            Entity(
                project_id=project.id,
                type="character",
                code="c02",
                name="Инспектор",
            ),
        ]
    )
    await session.flush()

    refs_before, used_parent_before = await _montage_shot1_refs(
        session, project, child
    )
    assert used_parent_before is True
    assert any(p.name.startswith("frame_001") for p in refs_before)

    await apply_coverage_op(
        session,
        project,
        {"type": "coverage_kind", "frame_number": 2, "kind": "parent"},
    )
    rows = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.number)
            )
        )
        .scalars()
        .all()
    )
    promoted = next(fr for fr in rows if int(fr.number) == 2)
    assert is_shot_child(promoted) is True
    found = find_coverage_parent_frame(rows, promoted)
    assert found is None
    assert (promoted.attrs or {}).get("characters") == "c02"

    refs_after, used_parent_after = await _montage_shot1_refs(
        session, project, promoted
    )
    assert used_parent_after is False
    assert not any(p.name.startswith("frame_001") for p in refs_after)

    board = await build_montage_board(session, project)
    c_row = next(fr for fr in board["frames"] if fr["number"] == 2)
    p_row = next(fr for fr in board["frames"] if fr["number"] == 1)
    assert c_row["shot_kind"] == "parent"
    assert c_row["shot_parent_number"] is None
    assert c_row["ref_parent"] is None
    assert c_row["vo_scene_number"] == 1
    assert p_row["vo_scene_number"] == 1
    assert p_row["vo_scene_size"] == 2
    chars_row = c_row["group_character_refs"]
    assert [r["id"] for r in chars_row] == ["c02"]
    assert chars_row[0]["code"] == "c02"
    assert chars_row[0]["name"] == "Инспектор"


@pytest.mark.asyncio
async def test_kind_change_does_not_split_vo_scene(
    session: AsyncSession, project: Project
) -> None:
    """Родитель/дочерний меняет still, не членство в VO-сцене."""
    parent, child = _parent_child(project.id)
    sib_uid = "dd" * 12
    sibling = Frame(
        project_id=project.id,
        number=3,
        uuid=sib_uid,
        voiceover_text="третий шот",
        status="planned",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent.uuid,
                "coverage_parent_id": "1-K1",
            },
        },
    )
    other = Frame(
        project_id=project.id,
        number=9,
        uuid="cc" * 12,
        voiceover_text="другая ячейка",
        status="planned",
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": "cc" * 12,
                "shot_id": "9-K1",
            }
        },
    )
    session.add_all([project, parent, child, sibling, other])
    await session.flush()

    await apply_coverage_op(
        session,
        project,
        {"type": "coverage_kind", "frame_number": 2, "kind": "parent"},
    )
    board = await build_montage_board(session, project)
    by_n = {fr["number"]: fr for fr in board["frames"]}
    assert by_n[1]["vo_scene_number"] == 1
    assert by_n[2]["vo_scene_number"] == 1
    assert by_n[3]["vo_scene_number"] == 1
    assert by_n[1]["vo_scene_size"] == 3
    assert by_n[2]["shot_kind"] == "parent"
    assert by_n[9]["vo_scene_number"] == 9

    await apply_coverage_op(
        session,
        project,
        {
            "type": "coverage_kind",
            "frame_number": 1,
            "kind": "child",
            "parent_number": 9,
        },
    )
    board = await build_montage_board(session, project)
    by_n = {fr["number"]: fr for fr in board["frames"]}
    assert by_n[1]["vo_scene_number"] == 1
    assert by_n[2]["vo_scene_number"] == 1
    assert by_n[3]["vo_scene_number"] == 1
    assert by_n[1]["vo_scene_size"] == 3
    assert by_n[1]["shot_kind"] == "child"
    assert by_n[1]["shot_parent_number"] == 9
    assert by_n[9]["vo_scene_number"] == 9
    assert by_n[9]["vo_scene_size"] == 1


@pytest.mark.asyncio
async def test_apply_scene_action_explodes_shots(
    session: AsyncSession, project: Project
) -> None:
    parent = Frame(
        project_id=project.id,
        number=5,
        uuid="ee" * 12,
        voiceover_text="Следователь вошёл в архив и снял папку с полки.",
        duration_seconds=8,
        status="planned",
        attrs={
            "place": "архив",
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": "ee" * 12,
                "место": "архив",
            },
        },
    )
    session.add_all([project, parent])
    await session.flush()

    result = await apply_coverage_op(
        session,
        project,
        {
            "type": "coverage_scene_action",
            "frame_number": 5,
            "action": "следователь входит в архив, достаёт папку и читает документ",
        },
    )
    assert result["ok"] is True
    assert result["highlight"] == "5:scene_action"
    assert result["regen_image"] is False
    assert result["report"]["shots"] >= 1

    rows = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
    head = next(fr for fr in rows if int(fr.number) == 5)
    chain = str((head.attrs or {}).get("главное_действие") or "")
    assert chain.startswith("1.")
    kadry = (head.attrs or {}).get("кадры") or []
    assert isinstance(kadry, list) and kadry
    vo_join = " ".join(
        " ".join((fr.voiceover_text or "").split())
        for fr in rows
        if " ".join((fr.voiceover_text or "").split())
    )
    assert "архив" in vo_join.casefold()
    assert int(result["report"]["inserted_frames"]) == 0
    assert "renumber" not in result
    assert [int(fr.number) for fr in rows] == [5]
    if len(kadry) > 1:
        assert int(result["report"]["skipped_shots"]) == len(kadry) - 1


@pytest.mark.asyncio
async def test_apply_scene_action_splits_director_prose(
    session: AsyncSession, project: Project
) -> None:
    parent_uid = "ee" * 12
    child_uid = "ff" * 12
    parent = Frame(
        project_id=project.id,
        number=14,
        uuid=parent_uid,
        voiceover_text="Крепостной крестьянин полностью зависел от помещика.",
        duration_seconds=3.71,
        status="planned",
        attrs={
            "place": "двор помещичьей усадьбы",
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "место": "двор помещичьей усадьбы",
            },
        },
    )
    child = Frame(
        project_id=project.id,
        number=15,
        uuid=child_uid,
        voiceover_text=(
            "Его записывали как «душу», продавали вместе с землёй, "
            "переселяли, наказывали и заставляли работать по воле хозяина."
        ),
        duration_seconds=8.21,
        status="planned",
        attrs={
            "shot01_action": "старый приказчик со списком",
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "coverage_parent_id": "14-K1",
            },
        },
    )
    neighbor = Frame(
        project_id=project.id,
        number=16,
        uuid="aa" * 12,
        voiceover_text="Закон существовал, однако внутри усадьбы власть помещика.",
        duration_seconds=4,
        status="planned",
        attrs={"shot01_action": "лицо приказчика"},
    )
    session.add_all([project, parent, child, neighbor])
    await session.flush()

    result = await apply_coverage_op(
        session,
        project,
        {
            "type": "coverage_scene_action",
            "frame_number": 14,
            "action": (
                "покажи сцену как набор кадров, крепостной стоит опустив голову "
                "и слушает как на него кричит помещик. нужно потом показать, "
                "как его с семьей и землей один помещник продал другому. "
                "как его наказывали потом и заставляли работать"
            ),
        },
    )
    assert result["ok"] is True
    assert int(result["report"]["inserted_frames"]) == 0
    assert "renumber" not in result
    rows = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
    assert [int(fr.number) for fr in rows] == [14, 15, 16]
    head = next(fr for fr in rows if str(fr.uuid) == parent_uid)
    kadry = head.attrs.get("кадры") or []
    acts = [str(s.get("действие") or "") for s in kadry]
    assert len(acts) >= 3
    assert all("вход: видно всё помещение" not in a for a in acts)
    assert any("кричит" in a for a in acts)
    assert int(result["report"]["shots"]) == 2
    assert int(result["report"]["skipped_shots"]) == len(kadry) - 2
    kids = [fr for fr in rows if str(fr.uuid) == child_uid]
    assert kids
    assert "приказчик" not in str((kids[0].attrs or {}).get("shot01_action") or "")
    assert is_shot_child(kids[0]) is True
    later = next(fr for fr in rows if int(fr.number) == 16)
    assert later.voiceover_text.startswith("Закон существовал")
    assert str((later.attrs or {}).get("shot01_action") or "") == "лицо приказчика"


@pytest.mark.asyncio
async def test_apply_scene_action_rejects_empty(
    session: AsyncSession, project: Project
) -> None:
    parent, _child = _parent_child(project.id)
    session.add_all([project, parent])
    await session.flush()
    with pytest.raises(RuntimeError, match="последовательность кадров пустая"):
        await apply_coverage_op(
            session,
            project,
            {"type": "coverage_scene_action", "frame_number": 1, "action": "  "},
        )


@pytest.mark.asyncio
async def test_apply_scene_action_rewrites_arrows_and_clears_extra(
    session: AsyncSession, project: Project
) -> None:
    """Новая цепь по ``→`` сменяет действия шотов; лишние не оставляют старый шаг."""
    parent_uid = "aa" * 12
    parent = Frame(
        project_id=project.id,
        number=1,
        uuid=parent_uid,
        voiceover_text="Он вошёл и ушёл.",
        status="planned",
        attrs={
            "shot01_action": "старый вход",
            "камера_place": "двор",
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "место": "двор",
                "действие": "старый вход",
            },
        },
    )
    child_a = Frame(
        project_id=project.id,
        number=2,
        uuid="bb" * 12,
        voiceover_text="кусок",
        status="planned",
        attrs={
            "shot01_action": "старый жест",
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "действие": "старый жест",
            },
        },
    )
    child_b = Frame(
        project_id=project.id,
        number=3,
        uuid="cc" * 12,
        voiceover_text="хвост",
        status="planned",
        attrs={
            "shot01_action": "старый уход",
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "действие": "старый уход",
            },
        },
    )
    session.add_all([project, parent, child_a, child_b])
    await session.flush()

    result = await apply_coverage_op(
        session,
        project,
        {
            "type": "coverage_scene_action",
            "frame_number": 1,
            "action": (
                "1. двор — душит → уходит скрытно\n"
                "(Он вошёл и ушёл.)"
            ),
        },
    )
    assert result["ok"] is True
    kadry = (parent.attrs or {}).get("кадры") or []
    acts = [str(s.get("действие") or "") for s in kadry]
    assert acts[:2] == ["душит", "уходит скрытно"]
    assert str((parent.attrs or {}).get("shot01_action") or "") == "душит"
    assert str((child_a.attrs or {}).get("shot01_action") or "") == "уходит скрытно"
    assert str((child_b.attrs or {}).get("shot01_action") or "") == ""
    from app.services.montage_scene_editor import shot_sequence_text

    seq = shot_sequence_text(parent, [parent, child_a, child_b])
    assert seq.startswith("душит → уходит скрытно")
    assert "старый" not in seq
    assert bool((child_b.attrs or {}).get("camera_subdivide", {}).get("leftover")) is True
    assert not bool((parent.attrs or {}).get("camera_subdivide", {}).get("leftover"))


@pytest.mark.asyncio
async def test_apply_scene_action_grow_inserts_without_renumber(
    session: AsyncSession, project: Project
) -> None:
    """Improve: новые шоты в конец ячейки, number соседей не двигаем."""
    from app.services.montage_coverage_ops import apply_coverage_scene_action
    from app.services.montage_scene_editor import scene_group

    parent_uid = "aa" * 12
    parent = Frame(
        project_id=project.id,
        number=14,
        uuid=parent_uid,
        voiceover_text="Следователь вошёл в архив, снял папку и читает документ.",
        status="planned",
        sort_key=14.0,
        attrs={
            "place": "архив",
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "место": "архив",
            },
        },
    )
    child = Frame(
        project_id=project.id,
        number=15,
        uuid="bb" * 12,
        voiceover_text="снял папку",
        status="planned",
        sort_key=15.0,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "coverage_parent_id": "14-K1",
            },
        },
    )
    neighbor = Frame(
        project_id=project.id,
        number=16,
        uuid="cc" * 12,
        voiceover_text="Закон существовал.",
        status="planned",
        sort_key=16.0,
        attrs={"shot01_action": "лицо приказчика"},
    )
    session.add_all([project, parent, child, neighbor])
    await session.flush()
    action = (
        "1. архив — входит в помещение\n"
        "(Следователь вошёл в архив,)\n"
        "2. архив — снимает папку с полки\n"
        "(снял папку)\n"
        "3. архив — читает документ\n"
        "(и читает документ.)\n"
        "4. архив — смотрит на печать\n"
        "(на обложке.)"
    )
    report = await apply_coverage_scene_action(
        session, project, parent, [parent, child, neighbor], action, grow=True
    )
    assert int(report["inserted_frames"]) == 2
    assert int(report["skipped_shots"]) == 0
    assert not report.get("renumber")
    rows = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
    later = next(fr for fr in rows if int(fr.number) == 16)
    assert later.voiceover_text.startswith("Закон")
    assert str((later.attrs or {}).get("shot01_action") or "") == "лицо приказчика"
    _parent, group = scene_group(rows, parent)
    assert len(group) == 4
    ordered = [int(fr.number) for fr in rows]
    assert ordered[:2] == [14, 15]
    assert ordered[-1] == 16
    fresh = [n for n in ordered if n not in (14, 15, 16)]
    assert len(fresh) == 2
    assert min(fresh) > 16
    from app.services.vo_shot_expand import _cs

    for fr in rows:
        if int(fr.number) in fresh:
            assert _cs(fr).get("role") == "shot"
            assert _cs(fr).get("parent_uuid") == parent_uid
            assert _cs(fr).get("coverage_kind") == "child"
    live_parent = next(fr for fr in rows if int(fr.number) == 14)
    assert _cs(live_parent).get("coverage_kind") == "parent"
    assert (_cs(live_parent).get("план") or live_parent.attrs.get("крупность")) == "ОБЩИЙ"


@pytest.mark.asyncio
async def test_scene_action_keeps_medium_parent_plan(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_coverage_ops import apply_coverage_scene_action
    from app.services.montage_scene_editor import scene_group
    from app.services.vo_shot_expand import _cs

    parent_uid = "dd" * 12
    parent = Frame(
        project_id=project.id,
        number=1,
        uuid=parent_uid,
        voiceover_text="Ткач стоит у станка и чинит челнок.",
        status="planned",
        sort_key=1.0,
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "место": "цех",
            },
        },
    )
    session.add_all([project, parent])
    await session.flush()
    kadry = [
        {
            "id": "1-K1",
            "план": "СРЕДНИЙ",
            "действие": "место и ткач лицом к камере",
            "закадр": "Ткач стоит у станка",
        },
        {
            "id": "1-K2",
            "план": "КРУПНЫЙ",
            "действие": "чинит челнок",
            "закадр": "и чинит челнок.",
            "parent_id": "1-K1",
        },
    ]
    await apply_coverage_scene_action(
        session,
        project,
        parent,
        [parent],
        "1. цех — место\n(Ткач стоит у станка)\n2. цех — чинит\n(и чинит челнок.)",
        grow=True,
        kadry=kadry,
    )
    frames = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
    live = next(fr for fr in frames if int(fr.number) == 1)
    assert _cs(live).get("план") == "СРЕДНИЙ"
    _parent, group = scene_group(frames, live)
    assert len(group) == 2
    assert _cs(group[1]).get("coverage_kind") == "child"


@pytest.mark.asyncio
async def test_improve_does_not_turn_leftover_glue_into_new_scenes(
    session: AsyncSession, project: Project
) -> None:
    """Хвост leftover другой ячейки не становится новыми «Сцена · кадр»."""
    from app.services.montage_coverage_ops import apply_coverage_scene_action
    from app.services.montage_scene_editor import scene_group
    from app.services.vo_shot_expand import _cs

    parent_uid = "aa" * 12
    other_uid = "bb" * 12
    parent = Frame(
        project_id=project.id,
        number=1,
        uuid=parent_uid,
        voiceover_text="Он вышел в лес.",
        status="planned",
        sort_key=10.0,
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "место": "лес",
            },
        },
    )
    neighbor = Frame(
        project_id=project.id,
        number=2,
        uuid=other_uid,
        voiceover_text="Потом вернулся домой.",
        status="planned",
        sort_key=20.0,
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": other_uid,
            },
        },
    )
    leftover = Frame(
        project_id=project.id,
        number=7,
        uuid="cc" * 12,
        voiceover_text="хвост",
        status="planned",
        sort_key=30.0,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "leftover": True,
            },
        },
    )
    session.add_all([project, parent, neighbor, leftover])
    await session.flush()
    action = (
        "1. лес — выходит из чащи\n(Он вышел в лес.)\n"
        "2. лес — скрывается в тени\n(скрылся.)\n"
        "3. лес — смотрит на тропу\n(на тропе.)"
    )
    report = await apply_coverage_scene_action(
        session,
        project,
        parent,
        [parent, neighbor, leftover],
        action,
        grow=True,
    )
    assert int(report["inserted_frames"]) == 2
    rows = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
    glue = next(fr for fr in rows if int(fr.number) == 7)
    assert bool(_cs(glue).get("leftover")) is True
    home = next(fr for fr in rows if int(fr.number) == 2)
    assert _cs(home).get("role") == "vo_parent"
    assert home.voiceover_text.startswith("Потом")
    ordered = [int(fr.number) for fr in rows]
    assert ordered[0] == 1
    assert ordered[-1] == 7
    assert 2 in ordered
    live_parent, members = scene_group(rows, parent)
    live = [m for m in members if not bool(_cs(m).get("leftover"))]
    assert live_parent.uuid == parent_uid
    assert len(live) == 3
    assert all(
        _cs(m).get("role") == "shot" and _cs(m).get("parent_uuid") == parent_uid
        for m in live
        if int(m.number) != 1
    )
    assert all(_cs(m).get("role") != "vo_parent" or m.uuid == parent_uid for m in live)
    # новые шоты стоят сразу после родителя, до соседней сцены
    assert ordered[:4] == [1, ordered[1], ordered[2], 2]
    assert not (_cs(glue).get("план") or _cs(glue).get("крупность"))
    assert not (_cs(glue).get("ракурс") or _cs(glue).get("движение"))


@pytest.mark.asyncio
async def test_scene_action_clears_leftover_chips(
    session: AsyncSession, project: Project
) -> None:
    """Хвост leftover не держит старые крупность/ракурс после новой цепи."""
    from app.services.montage_coverage_ops import apply_coverage_scene_action

    parent_uid = "dd" * 12
    parent = Frame(
        project_id=project.id,
        number=10,
        uuid=parent_uid,
        voiceover_text="Он стоит у двери.",
        status="planned",
        sort_key=10.0,
        attrs={
            "действие": "стоит у двери",
            "крупность": "СРЕДНИЙ",
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "план": "СРЕДНИЙ",
                "ракурс": "фронт",
                "движение": "статика",
            },
        },
    )
    child = Frame(
        project_id=project.id,
        number=11,
        uuid="ee" * 12,
        voiceover_text="смотрит.",
        status="planned",
        sort_key=11.0,
        attrs={
            "действие": "смотрит в щёлку",
            "крупность": "СРЕДНИЙ",
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "план": "СРЕДНИЙ",
                "ракурс": "фронт",
                "движение": "статика",
            },
        },
    )
    leftover = Frame(
        project_id=project.id,
        number=12,
        uuid="ff" * 12,
        voiceover_text="хвост",
        status="planned",
        sort_key=12.0,
        attrs={
            "действие": "старое крупное",
            "крупность": "КРУПНЫЙ",
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "leftover": True,
                "план": "КРУПНЫЙ",
                "ракурс": "с плеча",
                "движение": "статика",
            },
        },
    )
    session.add_all([project, parent, child, leftover])
    await session.flush()
    kadry = [
        {
            "id": "10-S1-K1",
            "действие": "стоит у двери",
            "план": "ОБЩИЙ",
            "ракурс": "фронт",
            "движение": "статика",
            "закадр": "Он стоит у двери.",
        },
        {
            "id": "10-S1-K2",
            "действие": "смотрит в щёлку",
            "план": "КРУПНЫЙ",
            "ракурс": "3/4",
            "движение": "статика",
            "закадр": "смотрит.",
        },
    ]
    await apply_coverage_scene_action(
        session,
        project,
        parent,
        [parent, child, leftover],
        "1. дом — стоит у двери\n2. дом — смотрит в щёлку",
        grow=True,
        kadry=kadry,
        lock_parent_plan=False,
    )
    assert _cs(leftover).get("leftover") is True
    assert not (_cs(leftover).get("план") or leftover.attrs.get("крупность"))
    assert not _cs(leftover).get("ракурс")
    assert not _cs(leftover).get("angle")
    assert not _cs(leftover).get("move")
    assert _cs(parent).get("план") == "ОБЩИЙ"
    assert _cs(child).get("план") == "КРУПНЫЙ"
    leftover.attrs = {
        **dict(leftover.attrs or {}),
        "camera_subdivide": {
            **_cs(leftover),
            "leftover": True,
            "angle": "3/4",
            "move": "панорама",
        },
    }
    from app.services.montage_board import _plan_for_frame, _shot_cs_kadry

    assert _plan_for_frame(leftover) == ""
    assert _shot_cs_kadry(leftover, "ракурс", "angle") == ""
    assert _shot_cs_kadry(leftover, "движение", "move") == ""


@pytest.mark.asyncio
async def test_apply_vo_span_saves_scene_text(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_scene_editor import cell_scene_text, scene_group

    parent_uid = "aa" * 12
    full = "Он вошёл в архив и снял папку с полки."
    parent = Frame(
        project_id=project.id,
        number=1,
        uuid=parent_uid,
        voiceover_text=full,
        status="planned",
        attrs={
            "vo_cell_full": full,
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
            },
        },
    )
    session.add_all([project, parent])
    await session.flush()
    result = await apply_coverage_op(
        session,
        project,
        {
            "type": "coverage_vo_span",
            "frame_number": 1,
            "shot": 1,
            "start": 0,
            "end": 17,
            "text": "Он вошёл в архив",
        },
    )
    assert result["ok"] is True
    assert result["highlight"] == "1:vo_span"
    assert result["regen_image"] is False
    await session.refresh(parent)
    group_parent, members = scene_group([parent], parent)
    assert cell_scene_text(group_parent, members) == "Он вошёл в архив"


@pytest.mark.asyncio
async def test_apply_vo_span_writes_edited_full_text(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_scene_editor import cell_scene_text, scene_group

    parent = Frame(
        project_id=project.id,
        number=1,
        uuid="bb" * 12,
        voiceover_text="Старый закадр.",
        status="planned",
        attrs={"vo_cell_full": "Старый закадр."},
    )
    session.add_all([project, parent])
    await session.flush()
    await apply_coverage_op(
        session,
        project,
        {
            "type": "coverage_vo_span",
            "frame_number": 1,
            "shot": 1,
            "start": 0,
            "end": 13,
            "text": "Новый закадр.",
            "full": "Новый закадр.",
        },
    )
    await session.refresh(parent)
    assert (parent.attrs or {}).get("vo_cell_full") == "Новый закадр."
    group_parent, members = scene_group([parent], parent)
    assert cell_scene_text(group_parent, members) == "Новый закадр."


@pytest.mark.asyncio
async def test_apply_vo_span_writes_edited_full_text(
    session: AsyncSession, project: Project
) -> None:
    from app.services.montage_scene_editor import cell_scene_text, scene_group

    parent = Frame(
        project_id=project.id,
        number=1,
        uuid="bb" * 12,
        voiceover_text="Старый закадр.",
        status="planned",
        attrs={"vo_cell_full": "Старый закадр."},
    )
    session.add_all([project, parent])
    await session.flush()
    await apply_coverage_op(
        session,
        project,
        {
            "type": "coverage_vo_span",
            "frame_number": 1,
            "shot": 1,
            "start": 0,
            "end": 13,
            "text": "Новый закадр.",
            "full": "Новый закадр.",
        },
    )
    await session.refresh(parent)
    assert (parent.attrs or {}).get("vo_cell_full") == "Новый закадр."
    group_parent, members = scene_group([parent], parent)
    assert cell_scene_text(group_parent, members) == "Новый закадр."

