"""«Улучшить сцену»: одна ячейка через 6 нод группы + режиссёрская достройка.

Якоря сцены — граница текста: кадры якоря берут закадр только из его куска.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.montage_board import _coverage_fields_for_frames, _snapshot_frames
from app.services.montage_scene_editor import split_vo_by_anchors
from app.services.montage_scene_improve import (
    GROUP_NODES,
    REPORT_ATTR,
    allot_budgets,
    anchor_units,
    check_bits,
    improve_cell_scene,
    merge_passport,
    normalize_shots,
    qc_reasons,
    repair_shots,
    shot_budget,
    split_vo_for_shots,
)

PIECE_1 = "Он пришёл домой поздно ночью и оставил на полу в коридоре старый носок."
PIECE_2 = "Через минуту к нему зашла девушка, увидела носок, закричала и убежала."
VO = f"{PIECE_1} {PIECE_2}"
ANCHOR_1 = "Он пришёл домой поздно"
ANCHOR_2 = "Через минуту к нему"
UID = "ab" * 12

# (якорь, зона, действие, роль, объект, план, ракурс, движение, стык)
STEPS = [
    (1, "крыльцо", "подходит к дому", "вход", "место", "ОБЩИЙ", "фронт", "следование", "cut"),
    (1, "дверь", "рука открывает дверь", "мост", "предмет", "ДЕТАЛЬ", "сверху", "статика", "cut_on_action"),
    (1, "прихожая", "закрывает дверь изнутри", "мост", "тело", "СРЕДНИЙ", "3/4", "статика", "cut_on_action"),
    (1, "коридор", "проходит вперёд, достаёт носок", "действие", "тело", "СРЕДНИЙ", "фронт", "следование", "cut_on_action"),
    (1, "коридор", "кладёт носок на пол", "действие", "предмет", "ДЕТАЛЬ", "сверху", "статика", "cut_on_action"),
    (2, "коридор", "девушка крадётся по коридору", "мост", "тело", "СРЕДНИЙ", "3/4", "следование", "cut"),
    (2, "коридор", "девушка входит к нему", "действие", "двое", "СРЕДНИЙ", "с плеча", "статика", "cut_on_action"),
    (2, "коридор", "лицо девушки: испуг", "реакция", "лицо", "КРУПНЫЙ", "фронт", "наезд", "cut"),
    (2, "коридор", "девушка кричит", "действие", "лицо", "КРУПНЫЙ", "3/4", "статика", "cut"),
    (2, "коридор", "девушка убегает", "действие", "тело", "ОБЩИЙ", "3/4", "ручная", "cut_on_action"),
    (2, "коридор", "он бежит за ней", "следствие", "тело", "СРЕДНИЙ", "3/4", "следование", "cut_on_action"),
]


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'improve.db'}")
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
    return Project(id=71, slug="sock-house", topic="t", hero_mode="auto")


def _cell(project: Project, *, bits: list[dict] | None = None) -> tuple[Frame, Frame]:
    attrs = {
        "place": "дом",
        "vo_cell_full": VO,
        "главное_действие": (
            "1. дом — пришёл в дом → положил носок → зашла девушка → закричала → убегает\n"
            f"({VO})"
        ),
        "camera_subdivide": {"role": "vo_parent", "parent_uuid": UID, "место": "дом"},
    }
    if bits is not None:
        attrs["биты"] = bits
    parent = Frame(
        project_id=project.id,
        number=3,
        uuid=UID,
        voiceover_text=VO,
        status="planned",
        sort_key=3.0,
        attrs=attrs,
    )
    neighbor = Frame(
        project_id=project.id,
        number=4,
        uuid="cd" * 12,
        voiceover_text="Утром дом был пуст.",
        status="planned",
        sort_key=4.0,
        attrs={"shot01_action": "пустой дом"},
    )
    return parent, neighbor


def _ops(fields: dict) -> str:
    return json.dumps({"ops": [{"frame_uuid": UID, "fields": fields}]}, ensure_ascii=False)


def _two_cards() -> str:
    one = " → ".join(s[2] for s in STEPS if s[0] == 1)
    two = " → ".join(s[2] for s in STEPS if s[0] == 2)
    return f"1. дом — {one}\n({PIECE_1})\n2. дом — {two}\n({PIECE_2})"


def _gpt_shots(pieces_by_anchor: dict[int, list[str]]) -> list[dict]:
    counters = {1: 0, 2: 0}
    out = []
    for a, zone, act, role, obj, plan, angle, move, stitch in STEPS:
        idx = counters[a]
        counters[a] += 1
        out.append(
            {
                "якорь_n": a,
                "место": f"дом: {zone}",
                "действие": act,
                "роль": role,
                "объект": obj,
                "план": plan,
                "ракурс": angle,
                "движение": move,
                "стык": stitch,
                "закадр": pieces_by_anchor[a][idx],
            }
        )
    return out


async def _cell_frames(session: AsyncSession, project: Project) -> list[Frame]:
    frames = list(
        (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.sort_key)
            )
        )
        .scalars()
        .all()
    )
    return frames


def _members(frames: list[Frame]) -> list[Frame]:
    return [
        fr
        for fr in frames
        if fr.uuid == UID
        or ((fr.attrs or {}).get("camera_subdivide") or {}).get("parent_uuid") == UID
    ]


# --------------------------------------------------------------------------- #
# Чистые функции
# --------------------------------------------------------------------------- #


def test_check_bits_requires_verbatim_ordered_anchors() -> None:
    bits = [
        {"порядок": 1, "изменение": "он пришёл домой", "якорь": ANCHOR_1},
        {"порядок": 2, "изменение": "зашла девушка", "якорь": ANCHOR_2},
    ]
    assert check_bits(VO, bits) is None
    assert "не найден" in (check_bits(VO, [{"порядок": 1, "изменение": "x", "якорь": "нет такого"}]) or "")
    assert check_bits(VO, []) == "нет битов"


def test_anchor_units_cut_like_board_and_reject_foreign_anchor() -> None:
    units = anchor_units(VO, [{"якорь": ANCHOR_1}, {"якорь": ANCHOR_2}])
    assert [u["закадр"] for u in units] == split_vo_by_anchors(VO, [ANCHOR_1, ANCHOR_2])
    assert [u["закадр"] for u in units] == [PIECE_1, PIECE_2]
    with pytest.raises(RuntimeError, match="текст других якорей"):
        anchor_units(VO, [{"якорь": ANCHOR_1}, {"якорь": "Утром дом был пуст"}])


def test_budgets_per_anchor() -> None:
    assert shot_budget(VO) == 12
    assert shot_budget("Он ушёл.") == 2
    assert allot_budgets([PIECE_1, PIECE_2]) == [6, 6]
    many = allot_budgets([VO] * 3)
    assert sum(many) <= 12 and min(many) >= 1


def test_split_vo_for_shots_never_empty_when_words_suffice() -> None:
    parts = split_vo_for_shots(VO, 11)
    assert len(parts) == 11
    assert all(parts)
    assert " ".join(parts) == VO


def test_normalize_maps_aliases_and_keeps_passport_place() -> None:
    shots = normalize_shots(
        [
            {"действие": "входит в кабинет", "место": "кабинет: порог", "план": "общий план", "ракурс": "фронтально", "стык": "прямая склейка"},
            {"действие": "берёт папку", "объект": "предмет", "план": "детальный", "ракурс": "из-за плеча", "стык": "по действию"},
            {"действие": "лицо: испуг", "объект": "лицо", "место": "другой дом"},
        ],
        cell_number=9,
        place="кабинет",
    )
    assert [s["план"] for s in shots] == ["ОБЩИЙ", "ДЕТАЛЬ", "КРУПНЫЙ"]
    assert [s["место"] for s in shots] == ["кабинет"] * 3
    assert shots[0]["зона"] == "порог"
    assert shots[0]["ракурс"] == "фронт"
    assert shots[1]["ракурс"] == "с плеча"
    assert shots[1]["стык"] == "cut_on_action"
    assert shots[0]["стык"] == "cut"
    assert shots[0]["роль"] == "вход"
    assert shots[2]["роль"] == "реакция"
    assert shots[1]["parent_id"] == "9-S1-K1"


def test_repair_keeps_each_anchor_text_inside_its_shots() -> None:
    units = anchor_units(VO, [{"якорь": ANCHOR_1}, {"якорь": ANCHOR_2}])
    shots = normalize_shots(
        [
            {"якорь_n": 1, "действие": "идёт к дому", "объект": "тело", "план": "СРЕДНИЙ", "ракурс": "3/4", "закадр": PIECE_1 + " Через минуту"},
            {"якорь_n": 1, "действие": "кладёт носок", "объект": "предмет", "план": "ДЕТАЛЬ", "ракурс": "с плеча", "закадр": ""},
            {"якорь_n": 2, "действие": "девушка входит", "объект": "тело", "план": "СРЕДНИЙ", "ракурс": "3/4", "закадр": "к нему зашла девушка,"},
            {"якорь_n": 2, "действие": "девушка убегает", "объект": "тело", "план": "СРЕДНИЙ", "ракурс": "3/4", "закадр": "увидела носок, закричала и убежала."},
        ],
        cell_number=1,
        place="дом",
        anchors=2,
    )
    fixed = repair_shots(shots, units)
    for unit in units:
        group = [s for s in shots if s["якорь_n"] == unit["n"]]
        assert " ".join(s["закадр"] for s in group) == unit["закадр"]
        assert all(s["закадр"] for s in group)
    assert shots[1]["ракурс"] == "сверху"
    assert shots[3]["ракурс"] != shots[2]["ракурс"]
    assert any("30°" in f for f in fixed)
    hard, _soft = qc_reasons(shots, units)
    assert hard == []


def test_qc_soft_warns_plan_jump_and_missing_reaction() -> None:
    text = "Он стоит у стола, потом у окна и садится."
    units = anchor_units(text, [{"якорь": "Он стоит у стола"}])
    shots = normalize_shots(
        [
            {"действие": "стоит у стола", "объект": "тело", "план": "ОБЩИЙ"},
            {"действие": "стоит у окна", "объект": "тело", "план": "ДЕТАЛЬ"},
            {"действие": "садится", "объект": "тело", "план": "СРЕДНИЙ"},
        ],
        cell_number=1,
        place="дом",
    )
    repair_shots(shots, units)
    _hard, soft = qc_reasons(shots, units)
    assert any("через план" in w for w in soft)
    assert any("реакции" in w for w in soft)


def test_merge_passport_keeps_operator_place_and_fills_empty() -> None:
    out, changed = merge_passport(
        {"place": "архив газет", "set": "архив газет", "characters": "c01, c02", "sense": ""},
        {
            "место": "другое место",
            "смысл": "герой находит статью",
            "свет": "ночной",
            "тип": "неизвестный стиль",
            "предметы": "газеты, лампа",
            "фон": "стеллажи",
            "акцент": "заголовок",
            "особенность": "тишина архива",
        },
    )
    assert out["place"] == "архив газет"
    assert out["characters"] == "c01, c02"
    assert out["sense"] == "герой находит статью"
    assert out["light"] == "ночной"
    assert "visual_type" not in out
    assert set(changed) >= {"sense", "props", "bg", "accent", "feature", "light"}
    assert "place" not in changed


# --------------------------------------------------------------------------- #
# Пайплайн на ячейке
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_improve_runs_six_nodes_and_board_shows_result(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, neighbor = _cell(project)
    session.add_all([project, parent, neighbor])
    await session.flush()
    prompts: list[str] = []
    pieces = {
        1: split_vo_for_shots(PIECE_1, 5),
        2: split_vo_for_shots(PIECE_2, 6),
    }

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        prompts.append(text)
        if "Агент: биты закадра" in text:
            return _ops(
                {
                    "биты": [
                        {"порядок": 1, "изменение": "он пришёл домой и оставил носок", "якорь": ANCHOR_1},
                        {"порядок": 2, "изменение": "девушка зашла, закричала, убежала", "якорь": ANCHOR_2},
                    ]
                }
            )
        if "Агент: главное действие" in text:
            return _ops(
                {
                    "главное_действие": _two_cards(),
                    "паспорт": {
                        "место": "чужое место",
                        "смысл": "покой дома → испуг и погоня",
                        "свет": "ночной",
                        "предметы": "носок, дверь",
                        "фон": "тёмный коридор",
                        "акцент": "лицо девушки",
                        "особенность": "саспенс: зритель видит её раньше героя",
                    },
                }
            )
        if "Агент: сцены → кадры" in text:
            return _ops({"кадры": _gpt_shots(pieces)})
        return json.dumps({"ops": []})

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)
    result = await improve_cell_scene(
        session,
        project,
        int(parent.id),
        passport={"place": "дом", "characters": "c01, c02"},
    )

    assert len(prompts) == 3
    assert all(UID in p for p in prompts)
    assert all("Утром дом был пуст" not in p for p in prompts)
    assert "Режиссура" in prompts[1] and "Режиссура" in prompts[2]
    assert "ровно 2 карточек" in prompts[1]

    report = result["improve_report"]
    assert [n["node"] for n in report["nodes"]] == [key for key, _ in GROUP_NODES]
    assert result["inserted_frames"] == len(STEPS) - 1
    assert result["images"] == len(STEPS)
    assert "Камера: план ОБЩИЙ" in result["image_ops"][0]["instruction"]
    assert "Часть места: крыльцо" in result["image_ops"][0]["instruction"]

    frames = await _cell_frames(session, project)
    cell = _members(frames)
    assert len(cell) == len(STEPS)
    assert " ".join(fr.voiceover_text or "" for fr in cell) == VO
    first_two = [fr.voiceover_text for fr in cell[:5]]
    assert " ".join(first_two) == PIECE_1

    board = _coverage_fields_for_frames(_snapshot_frames(frames), enabled=True)
    rows = [board[int(fr.number)] for fr in cell]
    assert [r["shot_plan"] for r in rows] == [s[5] for s in STEPS]
    assert [r["shot_angle"] for r in rows] == [s[6] for s in STEPS]
    assert [r["shot_move"] for r in rows] == [s[7] for s in STEPS]
    assert [r["shot_stitch"] for r in rows] == ["cut"] + [s[8] for s in STEPS[1:]]
    assert [r["shot_action"] for r in rows] == [s[2] for s in STEPS]
    head = rows[0]
    assert head["scene_place"] == "дом"
    assert head["scene_characters"] == "c01, c02"
    assert head["scene_sense"].startswith("покой дома")
    assert head["scene_bg"] == "тёмный коридор"
    assert head["scene_accent"] == "лицо девушки"
    assert head["scene_props"] == "носок, дверь"
    assert head["scene_feature"].startswith("саспенс")
    assert head["scene_lighting"] == "ночной"
    assert [c["vo"] for c in head["scene_chain"]] == [PIECE_1, PIECE_2]
    assert [a["якорь"] for a in head["scene_anchor_rows"]] == [ANCHOR_1, ANCHOR_2]
    assert all(r["scene_place"] == "дом" for r in rows)

    head_frame = next(fr for fr in cell if fr.uuid == UID)
    assert (head_frame.attrs or {}).get(REPORT_ATTR)
    assert neighbor.number == 4
    assert neighbor.voiceover_text.startswith("Утром")


@pytest.mark.asyncio
async def test_operator_anchors_are_kept_and_bound_the_text(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Один якорь на сцену: GPT биты не пишет, кадры берут только его кусок."""
    bits = [{"порядок": 1, "якорь": ANCHOR_1, "изменение": "герой дома", "главный": True}]
    parent, neighbor = _cell(project, bits=bits)
    session.add_all([project, parent, neighbor])
    await session.flush()
    prompts: list[str] = []

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        prompts.append(text)
        if "Агент: главное действие" in text:
            return _ops(
                {
                    "главное_действие": (
                        "1. дом — подходит к дому → открывает дверь\n(Он пришёл домой)\n"
                        "2. дом — девушка кричит\n(Утром дом был пуст.)"
                    )
                }
            )
        if "Агент: сцены → кадры" in text:
            return _ops(
                {
                    "кадры": [
                        {"действие": "подходит к дому", "закадр": "Он пришёл домой"},
                        {"действие": "открывает дверь", "закадр": "Утром дом был пуст."},
                        {"действие": "девушка кричит", "закадр": PIECE_2},
                    ]
                }
            )
        return json.dumps({"ops": []})

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)
    result = await improve_cell_scene(session, project, int(parent.id), passport={"place": "дом"})

    assert not any("Агент: биты закадра" in p for p in prompts)
    assert "ровно 1 карточек" in prompts[0]
    nodes = {n["node"]: n for n in result["improve_report"]["nodes"]}
    assert nodes["fw_script"]["status"] == "reused"
    frames = await _cell_frames(session, project)
    cell = _members(frames)
    glued = " ".join(fr.voiceover_text or "" for fr in cell)
    assert glued == VO
    assert "Утром" not in glued
    head = next(fr for fr in cell if fr.uuid == UID)
    assert [b["якорь"] for b in (head.attrs or {}).get("биты")] == [ANCHOR_1]
    assert neighbor.voiceover_text == "Утром дом был пуст."


@pytest.mark.asyncio
async def test_board_anchors_from_request_win_and_foreign_anchor_fails(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, neighbor = _cell(project, bits=[{"порядок": 1, "якорь": ANCHOR_1}])
    session.add_all([project, parent, neighbor])
    await session.flush()

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        return "нет ответа"

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)
    with pytest.raises(RuntimeError, match="якорь не найден"):
        await improve_cell_scene(
            session,
            project,
            int(parent.id),
            anchors=[{"якорь": ANCHOR_1}, {"якорь": "Утром дом был пуст"}],
        )
    result = await improve_cell_scene(
        session,
        project,
        int(parent.id),
        anchors=[{"якорь": ANCHOR_1}, {"якорь": ANCHOR_2}],
    )
    anchors = result["improve_report"]["anchors"]
    assert [a["закадр"] for a in anchors] == [PIECE_1, PIECE_2]
    shots = result["improve_report"]["shots"]
    for n, piece in ((1, PIECE_1), (2, PIECE_2)):
        assert " ".join(s["закадр"] for s in shots if s["якорь_n"] == n) == piece


@pytest.mark.asyncio
async def test_improve_without_gpt_still_goes_through_nodes(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, neighbor = _cell(project)
    session.add_all([project, parent, neighbor])
    await session.flush()

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        return "нет ответа"

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)
    result = await improve_cell_scene(session, project, int(parent.id), passport={"place": "дом"})
    nodes = {n["node"]: n["status"] for n in result["improve_report"]["nodes"]}
    assert list(nodes) == [key for key, _ in GROUP_NODES]
    assert nodes["fw_action"] == "fallback"
    assert nodes["fw_shots"] == "fallback"
    shots = result["improve_report"]["shots"]
    assert [s["действие"] for s in shots] == [
        "пришёл в дом",
        "положил носок",
        "зашла девушка",
        "закричала",
        "убегает",
    ]
    assert all(s["закадр"] for s in shots)
    assert result["inserted_frames"] == 4
