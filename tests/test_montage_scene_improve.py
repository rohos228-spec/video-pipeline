"""«Улучшить сцену»: прямой прогон ячейки + проверки нарезки/QC.

Закадр клеим без дыр. Персонажи — только из реестра и только если они в тексте.
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
    allot_budgets,
    anchor_units,
    build_improve_action_prompt,
    cards_from_operator_prompt,
    cards_raw_from_reply,
    check_bits,
    fallback_cards,
    improve_cell_scene,
    merge_passport,
    normalize_shots,
    operator_action_beats,
    qc_reasons,
    repair_shots,
    shot_budget,
    split_vo_for_shots,
    _ops_fields,
)

PIECE_1 = "Он пришёл домой поздно ночью и оставил на полу в коридоре старый носок."
PIECE_2 = "Через минуту к нему зашла девушка, увидела носок, закричала и убежала."
VO = f"{PIECE_1} {PIECE_2}"
ANCHOR_1 = "Он пришёл домой поздно"
ANCHOR_2 = "Через минуту к нему"
UID = "ab" * 12

# (якорь, зона, действие, роль, объект, план, ракурс, движение, стык)
STEPS = [
    (1, "коридор", "кладёт носок на пол", "действие", "предмет", "ДЕТАЛЬ", "сверху", "статика", "cut"),
    (2, "коридор", "девушка кричит и убегает", "действие", "тело", "СРЕДНИЙ", "3/4", "статика", "cut"),
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


def test_budgets_one_per_anchor_unless_operator_chain() -> None:
    assert shot_budget(VO) == 1
    assert shot_budget("Он ушёл.") == 1
    assert allot_budgets([PIECE_1, PIECE_2]) == [1, 1]
    assert allot_budgets([PIECE_1, PIECE_2], "стол → газета → экран") == [2, 1]
    many = allot_budgets([VO] * 3)
    assert many == [1, 1, 1]
    prose = "сначала газеты, потом экран. потом Банди улыбается в камеру"
    assert allot_budgets([PIECE_1, PIECE_2], prose) == [1, 1]


def test_improve_action_prompt_obeys_operator_and_skips_padding() -> None:
    parent, _ = _cell(Project(id=1, slug="t", topic="t", hero_mode="auto"))
    units = anchor_units(VO, [{"якорь": ANCHOR_1}, {"якорь": ANCHOR_2}])
    text = build_improve_action_prompt(
        parent=parent,
        vo=VO,
        units=units,
        passport={"место": "дом"},
        operator_prompt="только стол и газета, без телевизора",
    )
    assert "только стол и газета, без телевизора" in text
    assert "Поставь сцену" in text
    assert text.index("Поставь сцену") < text.index("Улучшить сцену — действие")
    assert "voiceover_text" not in text
    assert "текущая_цепь" not in text
    assert PIECE_1 not in text
    assert "добавь мосты" not in text
    assert "Режиссура" not in text
    assert "Каждый `→` станет кадром" not in text


def test_fallback_cards_follow_operator_chain() -> None:
    parent, _ = _cell(Project(id=1, slug="t", topic="t", hero_mode="auto"))
    units = anchor_units(VO, [{"якорь": ANCHOR_1}, {"якорь": ANCHOR_2}])
    cards = fallback_cards(parent, units, "стол → газета → экран", "архив")
    assert [c["action"] for c in cards] == ["стол", "газета", "экран"]
    assert " ".join(c["vo"] for c in cards) == VO
    prose = cards_from_operator_prompt("только носок, без Банди", "дом", VO)
    assert [c["action"] for c in prose] == ["только носок, без Банди"]
    assert prose[0]["vo"] == VO
    potom = cards_from_operator_prompt(
        "тед банди преследует девушку а потом на нее нападает", "улица", VO
    )
    assert [c["action"] for c in potom] == [
        "тед банди преследует девушку",
        "на нее нападает",
    ]


def test_cards_raw_from_flat_json_array_and_numbered() -> None:
    reply = json.dumps(
        {
            "главное_действие": [
                "идёт за девушкой по улице",
                "хватает её за плечо",
                "девушка лежит на земле",
            ],
            "паспорт": {"место": "улица"},
        },
        ensure_ascii=False,
    )
    fields = _ops_fields(reply, "")
    cards = cards_raw_from_reply(fields, reply)
    assert [c["action"] for c in cards] == [
        "идёт за девушкой по улице",
        "хватает её за плечо",
        "девушка лежит на земле",
    ]
    numbered = cards_raw_from_reply(
        {}, "1) идёт за девушкой\n2) хватает её\n3) лежит на земле"
    )
    assert [c["action"] for c in numbered] == [
        "идёт за девушкой",
        "хватает её",
        "лежит на земле",
    ]
    chain = "1. улица — идёт следом\n2. улица — хватает за плечо"
    wrapped_reply = _ops({"главное_действие": chain})
    wrapped = cards_raw_from_reply(_ops_fields(wrapped_reply, UID), wrapped_reply)
    assert [c["action"] for c in wrapped] == ["идёт следом", "хватает за плечо"]
    assert operator_action_beats("стол → газета") == ["стол", "газета"]


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
    assert [s["место"] for s in shots] == ["кабинет: порог", "кабинет", "кабинет: другой дом"]
    assert shots[0]["зона"] == "порог"
    assert shots[0]["ракурс"] == "фронт"
    assert shots[1]["ракурс"] == "с плеча"
    assert shots[1]["стык"] == "cut_on_action"
    assert shots[0]["стык"] == "cut"
    assert shots[0]["роль"] == "вход"
    assert shots[2]["роль"] == "реакция"
    assert shots[1]["parent_id"] == "9-S1-K1"


def test_repair_keeps_each_anchor_text_inside_its_shots() -> None:
    units = anchor_units(
        VO,
        [{"якорь": ANCHOR_1}, {"якорь": ANCHOR_2}],
        "идёт → носок → входит → убегает",
    )
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
    units = anchor_units(text, [{"якорь": "Он стоит у стола"}], "стоит → окно → садится")
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
    assert not any("реакции" in w for w in soft)
    assert not any("вводит место" in w for w in soft)


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
# Пайплайн на ячейке — прямой прогон
# --------------------------------------------------------------------------- #


def _registry(project: Project) -> list:
    from app.models import Entity

    return [
        Entity(project_id=project.id, type="character", code="c01", name="Ткач", sort_key=1.0),
        Entity(project_id=project.id, type="character", code="c02", name="девушка", sort_key=2.0),
        Entity(project_id=project.id, type="character", code="c03", name="Приказчик", sort_key=3.0),
    ]


@pytest.mark.asyncio
async def test_improve_matches_registry_and_keeps_medium_parent(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.vo_shot_expand import _cs

    parent, neighbor = _cell(project)
    session.add_all([project, parent, neighbor, *_registry(project)])
    await session.flush()
    prompts: list[str] = []

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        prompts.append(text)
        return json.dumps(
            {
                "сцена": "Ткач дома, девушка видит носок",
                "место": "дом",
                "персонажи": "c01, c02, c03",
                "план_родителя": "СРЕДНИЙ",
                "промт_картинки": "Средний план. Дом. Ткач и девушка лицом к камере.",
                "кадры": [
                    {"действие": "место и кто в кадре", "план": "СРЕДНИЙ"},
                    {"действие": "девушка кричит и убегает", "план": "СРЕДНИЙ"},
                ],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)
    result = await improve_cell_scene(
        session,
        project,
        int(parent.id),
        operator_prompt="ткач пришёл домой, потом крик девушки",
        passport={"place": "дом", "characters": "c01, c03"},
    )
    assert len(prompts) == 1
    assert "ткач пришёл домой" in prompts[0]
    assert VO in prompts[0]
    assert "Реестр персонажей" in prompts[0]
    assert "fw_script" not in prompts[0]
    nodes = [n["node"] for n in result["improve_report"]["nodes"]]
    assert nodes == ["prompt", "scene", "characters", "parent", "shots"]
    assert result["images"] == 0
    assert result["image_ops"] == []
    codes = [c["code"] for c in result["improve_report"]["characters"]]
    assert codes == ["c01", "c02"]
    glued = " ".join(s["закадр"] for s in result["improve_report"]["shots"])
    assert glued == VO
    frames = await _cell_frames(session, project)
    cell = _members(frames)
    assert " ".join(fr.voiceover_text or "" for fr in cell) == VO
    head = next(fr for fr in cell if fr.uuid == UID)
    assert "лицом к камере" in (head.image_prompt or "")
    assert _cs(head).get("план") == "СРЕДНИЙ"
    assert _cs(head).get("coverage_kind") == "parent"
    board = _coverage_fields_for_frames(_snapshot_frames(frames), enabled=True)
    rows = [board[int(fr.number)] for fr in cell]
    assert rows[0]["scene_characters"] == "c01, c02"
    assert neighbor.number == 4
    assert neighbor.voiceover_text.startswith("Утром")


@pytest.mark.asyncio
async def test_improve_does_not_attach_unmentioned_registry_name(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, neighbor = _cell(project)
    session.add_all([project, parent, neighbor, *_registry(project)])
    await session.flush()

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        return json.dumps(
            {"сцена": "крик", "персонажи": "c01, c02, c03", "план_родителя": "ОБЩИЙ"},
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)
    result = await improve_cell_scene(
        session, project, int(parent.id), passport={"place": "дом"}
    )
    codes = [c["code"] for c in result["improve_report"]["characters"]]
    assert codes == ["c02"]


@pytest.mark.asyncio
async def test_improve_without_gpt_still_splits_vo(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, neighbor = _cell(project)
    session.add_all([project, parent, neighbor, *_registry(project)])
    await session.flush()

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        return "нет ответа"

    monkeypatch.setattr("app.services.gpt_client.gpt_ask_fresh", fake_ask)
    result = await improve_cell_scene(
        session, project, int(parent.id), passport={"place": "дом"}
    )
    nodes = {n["node"]: n["status"] for n in result["improve_report"]["nodes"]}
    assert list(nodes) == ["prompt", "scene", "characters", "parent", "shots"]
    assert nodes["scene"] == "fallback"
    shots = result["improve_report"]["shots"]
    assert " ".join(s["закадр"] for s in shots) == VO
    assert "лицом к камере" in shots[0]["действие"]
    acts = [s["действие"] for s in shots]
    assert len(acts) == len({a.casefold() for a in acts})
    assert neighbor.voiceover_text.startswith("Утром")
