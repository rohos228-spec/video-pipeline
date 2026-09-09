"""Редактор сцены на доске монтажа: состояние, формат сцены, якоря, варианты."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.montage_board_apply import order_montage_pending_ops
from app.services.montage_board_meta import slot_key_from_op
from app.services.montage_coverage_ops import apply_coverage_op
from app.services.montage_scene_editor import (
    build_scene_editor_state,
    build_variant_prompt,
    normalize_anchor_rows,
    parse_variants,
    preview_template_ladder,
    split_vo_by_anchors,
)

VO_CELL = (
    "Он вошёл в кабинет следователя. Достал из портфеля папку. "
    "Открыл её на первой странице."
)


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scene.db'}")
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
    return Project(id=41, slug="scene-edit", topic="t", hero_mode="auto")


def _group(project_id: int) -> tuple[Frame, Frame]:
    """VO-родитель ячейки + один дочерний шот с кусками закадра."""
    parent_uid = "aa" * 12
    child_uid = "bb" * 12
    parent = Frame(
        project_id=project_id,
        number=1,
        uuid=parent_uid,
        sort_key=1.0,
        voiceover_text="Он вошёл в кабинет следователя.",
        status="planned",
        attrs={
            "vo_cell_full": VO_CELL,
            "shot01_action": "кабинет следователя. вошёл, снял пальто, сел к столу",
            "главное_действие": (
                "1. кабинет следователя — приносит папку и открывает дело\n"
                f"({VO_CELL})"
            ),
            "биты": [
                {"порядок": 1, "якорь": "Он вошёл в кабинет", "главный": True},
                {"порядок": 2, "якорь": "Достал из портфеля папку"},
            ],
            "кадры": [
                {
                    "id": "1-S1-K1",
                    "порядок": 1,
                    "parent_id": None,
                    "шаблон": "T2",
                    "план": "ОБЩИЙ",
                    "место": "кабинет следователя",
                    "действие": "кабинет следователя. вошёл, снял пальто, сел к столу",
                    "закадр": "Он вошёл в кабинет следователя.",
                },
                {
                    "id": "1-S1-K2",
                    "порядок": 2,
                    "parent_id": "1-S1-K1",
                    "шаблон": "T2",
                    "план": "ДЕТАЛЬ",
                    "место": "кабинет следователя",
                    "действие": "руки достают папку из портфеля, видна тесьма и номер дела",
                    "закадр": "Достал из портфеля папку. Открыл её на первой странице.",
                },
            ],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
                "shot_index": 1,
                "shots_in_beat": 2,
                "шаблон": "T2",
                "план": "ОБЩИЙ",
                "место": "кабинет следователя",
                "shot_id": "1-S1-K1",
            },
        },
    )
    child = Frame(
        project_id=project_id,
        number=2,
        uuid=child_uid,
        sort_key=2.0,
        voiceover_text="Достал из портфеля папку. Открыл её на первой странице.",
        status="planned",
        attrs={
            "shot01_action": "руки достают папку из портфеля, видна тесьма и номер дела",
            "кадры": [
                {
                    "id": "1-S1-K2",
                    "порядок": 1,
                    "parent_id": "1-S1-K1",
                    "шаблон": "T2",
                    "план": "ДЕТАЛЬ",
                    "место": "кабинет следователя",
                    "действие": "руки достают папку из портфеля, видна тесьма и номер дела",
                }
            ],
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": parent_uid,
                "coverage_parent_id": "1-S1-K1",
                "shot_index": 2,
                "shots_in_beat": 2,
                "шаблон": "T2",
                "план": "ДЕТАЛЬ",
                "место": "кабинет следователя",
                "shot_id": "1-S1-K2",
            },
        },
    )
    return parent, child


async def _frames(session: AsyncSession, project: Project) -> list[Frame]:
    return list(
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


# --- нарезка по якорям -------------------------------------------------


def test_split_vo_by_anchors_keeps_whole_text() -> None:
    parts = split_vo_by_anchors(
        VO_CELL, ["Он вошёл в кабинет", "Достал из портфеля папку", "Открыл её"]
    )
    assert len(parts) == 3
    assert " ".join(" ".join(parts).split()) == VO_CELL
    assert parts[1].startswith("Достал из портфеля папку")


def test_split_vo_by_anchors_first_part_starts_at_text_start() -> None:
    # Якорь стоит не на первом слове — начало ячейки всё равно не теряется.
    parts = split_vo_by_anchors(VO_CELL, ["Достал из портфеля папку", "Открыл её"])
    assert parts[0].startswith("Он вошёл")
    assert " ".join(" ".join(parts).split()) == VO_CELL


def test_split_vo_by_anchors_ignores_missing_anchor() -> None:
    parts = split_vo_by_anchors(VO_CELL, ["Он вошёл", "такого текста нет", "Открыл её"])
    assert len(parts) == 2
    assert " ".join(" ".join(parts).split()) == VO_CELL


def test_normalize_anchor_rows_single_main() -> None:
    rows = normalize_anchor_rows(
        [
            {"якорь": " Он вошёл ", "главный": True},
            {"якорь": "Достал папку", "главный": True},
            {"якорь": "   "},
        ]
    )
    assert [r["порядок"] for r in rows] == [1, 2]
    assert rows[0]["якорь"] == "Он вошёл"
    assert [r["главный"] for r in rows] == [True, False]


# --- состояние редактора ------------------------------------------------


@pytest.mark.asyncio
async def test_state_for_parent_frame(session: AsyncSession, project: Project) -> None:
    parent, child = _group(project.id)
    session.add_all([project, parent, child])
    await session.flush()
    frames = await _frames(session, project)

    state = build_scene_editor_state(frames, frames[0])
    assert state["frame"]["role"] == "parent"
    assert state["frame"]["number"] == 1
    assert state["group"] == [1, 2]
    assert state["vo"]["cell_full"] == VO_CELL
    assert state["plan"]["current"] == "ОБЩИЙ"
    assert "ДЕТАЛЬ" in state["plan"]["choices"]
    assert state["template"]["current"] == "T2"
    assert state["template"]["group_len"] == 2
    assert state["template"]["ladder"], "лестница шаблона обязана прийти в UI"
    assert {c["id"] for c in state["template"]["choices"]} >= {"T0", "T1", "T2", "X1"}
    assert state["scene_action"]["chain"][0]["place"] == "кабинет следователя"
    assert [b["якорь"] for b in state["anchors"]["bits"]] == [
        "Он вошёл в кабинет",
        "Достал из портфеля папку",
    ]
    assert all(b["found"] for b in state["anchors"]["bits"])
    assert state["anchors"]["covers_text"] is True
    assert [s["план"] for s in state["shots"]] == ["ОБЩИЙ", "ДЕТАЛЬ"]
    assert [s["frame_number"] for s in state["shots"]] == [1, 2]
    assert [p["number"] for p in state["parent_choices"]] == [2]


@pytest.mark.asyncio
async def test_state_for_child_points_to_parent(
    session: AsyncSession, project: Project
) -> None:
    parent, child = _group(project.id)
    session.add_all([project, parent, child])
    await session.flush()
    frames = await _frames(session, project)

    state = build_scene_editor_state(frames, frames[1])
    assert state["frame"]["role"] == "child"
    assert state["parent"]["number"] == 1
    assert state["plan"]["current"] == "ДЕТАЛЬ"
    # Якоря и полный текст ячейки берём с родителя, а не с куска ребёнка.
    assert state["vo"]["cell_full"] == VO_CELL
    assert len(state["anchors"]["bits"]) == 2


# --- формат сцены -------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_template_rewrites_ladder(
    session: AsyncSession, project: Project
) -> None:
    parent, child = _group(project.id)
    session.add_all([project, parent, child])
    await session.flush()

    result = await apply_coverage_op(
        session,
        project,
        {"type": "coverage_template", "frame_number": 1, "template": "T1"},
    )
    assert result["ok"] is True
    assert result["highlight"] == "1:template"
    report = result["report"]
    assert report["шаблон"] == "T1"
    assert report["frames"] == 2
    assert report["ladder"] == 5
    assert report["missing_frames"] == 3

    frames = await _frames(session, project)
    cs_parent = (frames[0].attrs or {})["camera_subdivide"]
    cs_child = (frames[1].attrs or {})["camera_subdivide"]
    assert cs_parent["шаблон"] == "T1"
    assert cs_child["шаблон"] == "T1"
    # T1: K1 = ОБЩИЙ master, K2 = СРЕДНИЙ сторона A.
    assert cs_parent["план"] == "ОБЩИЙ"
    assert cs_child["план"] == "СРЕДНИЙ"
    assert (frames[1].attrs or {})["крупность"] == "СРЕДНИЙ"
    ladder = (frames[0].attrs or {})["кадры"]
    assert [row["план"] for row in ladder] == ["ОБЩИЙ", "СРЕДНИЙ"]
    assert all(row["шаблон"] == "T1" for row in ladder)
    # Написанное вручную действие формат не затирает.
    assert "папку из портфеля" in (frames[1].attrs or {})["shot01_action"]


@pytest.mark.asyncio
async def test_coverage_template_unknown_rejected(
    session: AsyncSession, project: Project
) -> None:
    parent, child = _group(project.id)
    session.add_all([project, parent, child])
    await session.flush()
    with pytest.raises(RuntimeError, match="каталоге"):
        await apply_coverage_op(
            session,
            project,
            {"type": "coverage_template", "frame_number": 1, "template": "T99"},
        )


def test_preview_template_ladder_marks_missing_frames() -> None:
    state = {
        "frame": {"place": "кабинет"},
        "scene_action": {"chain": [{"action": "открывает дело"}]},
        "template": {"group_len": 2},
    }
    preview = preview_template_ladder(state, "T1")
    assert preview["шаблон"] == "T1"
    assert len(preview["rows"]) == 5
    assert [r["has_frame"] for r in preview["rows"]] == [True, True, False, False, False]
    assert preview["rows"][0]["план"] == "ОБЩИЙ"
    assert preview["rows"][0]["действие"], "превью действия не должно быть пустым"


# --- якоря --------------------------------------------------------------


@pytest.mark.asyncio
async def test_coverage_anchors_recut_voiceover(
    session: AsyncSession, project: Project
) -> None:
    parent, child = _group(project.id)
    session.add_all([project, parent, child])
    await session.flush()

    result = await apply_coverage_op(
        session,
        project,
        {
            "type": "coverage_anchors",
            "frame_number": 1,
            "anchors": [
                {"якорь": "Он вошёл в кабинет", "изменение": "снаружи → внутри"},
                {"якорь": "Открыл её на первой странице", "изменение": "папка → дело"},
            ],
        },
    )
    assert result["highlight"] == "1:anchors"
    # Якоря не меняют картинку — только нарезку закадра.
    assert result["regen_image"] is False
    assert result["report"]["assigned"] == 2

    frames = await _frames(session, project)
    assert frames[0].voiceover_text.startswith("Он вошёл в кабинет следователя.")
    assert frames[1].voiceover_text.startswith("Открыл её")
    joined = " ".join(
        " ".join(f"{fr.voiceover_text}" for fr in frames).split()
    )
    assert joined == VO_CELL
    bits = (frames[0].attrs or {})["биты"]
    assert [b["якорь"] for b in bits] == [
        "Он вошёл в кабинет",
        "Открыл её на первой странице",
    ]
    assert bits[0]["изменение"] == "снаружи → внутри"


@pytest.mark.asyncio
async def test_coverage_anchors_tail_goes_to_last_frame(
    session: AsyncSession, project: Project
) -> None:
    """Якорей больше, чем кадров: хвост текста не теряется."""
    parent, child = _group(project.id)
    session.add_all([project, parent, child])
    await session.flush()

    result = await apply_coverage_op(
        session,
        project,
        {
            "type": "coverage_anchors",
            "frame_number": 1,
            "anchors": [
                {"якорь": "Он вошёл в кабинет"},
                {"якорь": "Достал из портфеля папку"},
                {"якорь": "Открыл её на первой странице"},
            ],
        },
    )
    assert result["report"]["parts"] == 3
    assert result["report"]["missing_frames"] == 1
    frames = await _frames(session, project)
    joined = " ".join(" ".join(fr.voiceover_text for fr in frames).split())
    assert joined == VO_CELL
    assert frames[1].voiceover_text.startswith("Достал из портфеля папку")
    assert "Открыл её" in frames[1].voiceover_text


@pytest.mark.asyncio
async def test_coverage_anchors_empty_rejected(
    session: AsyncSession, project: Project
) -> None:
    parent, child = _group(project.id)
    session.add_all([project, parent, child])
    await session.flush()
    with pytest.raises(RuntimeError, match="якоря"):
        await apply_coverage_op(
            session,
            project,
            {"type": "coverage_anchors", "frame_number": 1, "anchors": []},
        )


# --- очередь ------------------------------------------------------------


def test_new_ops_are_coverage_phase() -> None:
    assert slot_key_from_op({"type": "coverage_template", "frame_number": 3}) == "3:template"
    assert slot_key_from_op({"type": "coverage_anchors", "frame_number": 3}) == "3:anchors"
    ordered = order_montage_pending_ops(
        [
            {"type": "image_regen", "frame_number": 1, "shot": 1},
            {"type": "coverage_anchors", "frame_number": 1, "anchors": [{"якорь": "a"}]},
            {"type": "coverage_template", "frame_number": 1, "template": "T1"},
        ]
    )
    assert [o["type"] for o in ordered][:2] == ["coverage_anchors", "coverage_template"]
    assert ordered[-1]["type"] == "image_regen"


# --- варианты -----------------------------------------------------------


@pytest.mark.asyncio
async def test_variant_prompts_carry_context(
    session: AsyncSession, project: Project
) -> None:
    parent, child = _group(project.id)
    session.add_all([project, parent, child])
    await session.flush()
    frames = await _frames(session, project)
    state = build_scene_editor_state(frames, frames[1])

    action_prompt = build_variant_prompt(
        state, kind="action", desc="руки крупно на тесьме папки", count=3
    )
    assert "руки крупно на тесьме папки" in action_prompt
    assert VO_CELL in action_prompt
    assert "дочерний шот" in action_prompt
    assert "варианты" in action_prompt

    tpl_prompt = build_variant_prompt(state, kind="template", count=2)
    assert "ШАБЛОНЫ СЦЕНА" in tpl_prompt
    assert "T1 — только речь" in tpl_prompt

    anchor_prompt = build_variant_prompt(state, kind="anchors", count=2)
    assert VO_CELL in anchor_prompt
    assert "ДОСЛОВНАЯ подстрока" in anchor_prompt


@pytest.mark.asyncio
async def test_parse_variants_filters_garbage(
    session: AsyncSession, project: Project
) -> None:
    parent, child = _group(project.id)
    session.add_all([project, parent, child])
    await session.flush()
    frames = await _frames(session, project)
    state = build_scene_editor_state(frames, frames[0])

    reply = "```json\n" + json.dumps(
        {
            "варианты": [
                {"действие": "кабинет. кладёт папку на стол", "план": "СРЕДНИЙ"},
                {"действие": "кабинет. кладёт папку на стол", "план": "СРЕДНИЙ"},
                {"действие": "лицо после удара", "план": "НЕТ ТАКОГО"},
                {"план": "КРУПНЫЙ"},
            ]
        },
        ensure_ascii=False,
    ) + "\n```"
    got = parse_variants(reply, kind="action", state=state)
    assert len(got) == 2
    assert got[0]["план"] == "СРЕДНИЙ"
    # Неизвестная крупность отбрасывается, само действие остаётся.
    assert got[1]["план"] == ""

    tpl = parse_variants(
        json.dumps({"варианты": [{"шаблон": "T5"}, {"шаблон": "Z9"}]}),
        kind="template",
        state=state,
    )
    assert [v["шаблон"] for v in tpl] == ["T5"]

    anchors = parse_variants(
        json.dumps(
            {
                "варианты": [
                    {
                        "биты": [
                            {"якорь": "Он вошёл в кабинет"},
                            {"якорь": "выдуманная фраза"},
                            {"якорь": "Открыл её"},
                        ]
                    }
                ]
            },
            ensure_ascii=False,
        ),
        kind="anchors",
        state=state,
    )
    assert len(anchors) == 1
    # Якорь, которого нет в тексте, выкидываем — иначе нарезка врёт.
    assert [b["якорь"] for b in anchors[0]["биты"]] == [
        "Он вошёл в кабинет",
        "Открыл её",
    ]
    assert anchors[0]["dropped"] == 1
    assert " ".join(" ".join(anchors[0]["preview"]).split()) == VO_CELL


def test_parse_variants_survives_non_json() -> None:
    state = {
        "plan": {"choices": ["ОБЩИЙ"]},
        "anchors": {"text": VO_CELL},
    }
    assert parse_variants("извини, не могу", kind="action", state=state) == []
