"""«Улучшить сцену»: одна ячейка через 6 нод группы + режиссёрская достройка."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.montage_scene_improve import (
    GROUP_NODES,
    REPORT_ATTR,
    check_bits,
    improve_cell_scene,
    merge_passport,
    normalize_shots,
    qc_reasons,
    repair_shots,
    shot_budget,
    split_vo_for_shots,
)

VO = (
    "Он пришёл домой поздно ночью и оставил на полу в коридоре старый носок. "
    "Через минуту к нему зашла девушка, увидела носок, закричала и убежала."
)
UID = "ab" * 12

STEPS = [
    ("дом: крыльцо", "подходит к дому", "вход", "место", "ОБЩИЙ", "фронт", "следование", "cut"),
    ("дом: дверь", "рука открывает дверь", "мост", "предмет", "ДЕТАЛЬ", "сверху", "статика", "cut_on_action"),
    ("дом: прихожая", "закрывает дверь изнутри", "мост", "тело", "СРЕДНИЙ", "3/4", "статика", "cut_on_action"),
    ("дом: коридор", "проходит вперёд, достаёт носок", "действие", "тело", "СРЕДНИЙ", "фронт", "следование", "cut_on_action"),
    ("дом: коридор", "кладёт носок на пол", "действие", "предмет", "ДЕТАЛЬ", "сверху", "статика", "cut_on_action"),
    ("дом: коридор", "девушка крадётся по коридору", "мост", "тело", "СРЕДНИЙ", "3/4", "следование", "cut"),
    ("дом: коридор", "девушка входит к нему", "действие", "двое", "СРЕДНИЙ", "с плеча", "статика", "cut_on_action"),
    ("дом: коридор", "лицо девушки: испуг", "реакция", "лицо", "КРУПНЫЙ", "фронт", "наезд", "cut"),
    ("дом: коридор", "девушка кричит", "действие", "лицо", "КРУПНЫЙ", "3/4", "статика", "cut"),
    ("дом: коридор", "девушка убегает", "действие", "тело", "ОБЩИЙ", "3/4", "ручная", "cut_on_action"),
    ("дом: коридор", "он бежит за ней", "следствие", "тело", "СРЕДНИЙ", "3/4", "следование", "cut_on_action"),
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


def _cell(project: Project) -> tuple[Frame, Frame]:
    parent = Frame(
        project_id=project.id,
        number=3,
        uuid=UID,
        voiceover_text=VO,
        status="planned",
        sort_key=3.0,
        attrs={
            "place": "дом",
            "главное_действие": (
                "1. дом — пришёл в дом → положил носок → зашла девушка → закричала → убегает\n"
                f"({VO})"
            ),
            "camera_subdivide": {"role": "vo_parent", "parent_uuid": UID, "место": "дом"},
        },
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


def test_check_bits_requires_verbatim_ordered_anchors() -> None:
    bits = [
        {"порядок": 1, "изменение": "он пришёл домой", "якорь": "Он пришёл домой"},
        {"порядок": 2, "изменение": "зашла девушка", "якорь": "Через минуту к нему"},
    ]
    assert check_bits(VO, bits) is None
    assert "не найден" in (check_bits(VO, [{"порядок": 1, "изменение": "x", "якорь": "нет такого"}]) or "")
    assert check_bits(VO, []) == "нет битов"


def test_shot_budget_caps_by_words_and_length() -> None:
    assert shot_budget(VO) == 12
    assert shot_budget("Он ушёл.") == 2
    assert shot_budget("") == 1


def test_split_vo_for_shots_never_empty_when_words_suffice() -> None:
    parts = split_vo_for_shots(VO, 11)
    assert len(parts) == 11
    assert all(parts)
    assert " ".join(parts) == " ".join(VO.split())


def test_normalize_maps_aliases_to_board_enums() -> None:
    shots = normalize_shots(
        [
            {"действие": "входит в кабинет", "план": "общий план", "ракурс": "фронтально", "стык": "прямая склейка"},
            {"действие": "берёт папку", "объект": "предмет", "план": "детальный", "ракурс": "из-за плеча", "стык": "по действию"},
            {"действие": "лицо: испуг", "объект": "лицо"},
        ],
        cell_number=9,
        place="кабинет",
    )
    assert [s["план"] for s in shots] == ["ОБЩИЙ", "ДЕТАЛЬ", "КРУПНЫЙ"]
    assert shots[0]["ракурс"] == "фронт"
    assert shots[1]["ракурс"] == "с плеча"
    assert shots[1]["стык"] == "cut_on_action"
    assert shots[0]["стык"] == "cut"
    assert shots[0]["роль"] == "вход"
    assert shots[2]["роль"] == "реакция"
    assert shots[1]["parent_id"] == "9-S1-K1"


def test_repair_fixes_30_degree_and_object_ots() -> None:
    shots = normalize_shots(
        [
            {"действие": "идёт по коридору", "объект": "тело", "план": "СРЕДНИЙ", "ракурс": "3/4"},
            {"действие": "останавливается у двери", "объект": "тело", "план": "СРЕДНИЙ", "ракурс": "3/4"},
            {"действие": "берёт ключ", "объект": "предмет", "план": "ДЕТАЛЬ", "ракурс": "с плеча"},
        ],
        cell_number=1,
        place="дом",
    )
    fixed = repair_shots(shots, "Он идёт по коридору и берёт ключ у двери.", cap=5)
    assert shots[1]["ракурс"] != shots[0]["ракурс"]
    assert shots[2]["ракурс"] == "сверху"
    assert any("30°" in f for f in fixed)
    hard, _soft = qc_reasons(shots, "Он идёт по коридору и берёт ключ у двери.")
    assert hard == []


def test_qc_soft_warns_plan_jump_and_missing_reaction() -> None:
    shots = normalize_shots(
        [
            {"действие": "стоит у стола", "объект": "тело", "план": "ОБЩИЙ"},
            {"действие": "стоит у окна", "объект": "тело", "план": "ДЕТАЛЬ"},
            {"действие": "садится", "объект": "тело", "план": "СРЕДНИЙ"},
        ],
        cell_number=1,
        place="дом",
    )
    repair_shots(shots, "Он стоит у стола, потом у окна и садится.", cap=5)
    _hard, soft = qc_reasons(shots, "Он стоит у стола, потом у окна и садится.")
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


@pytest.mark.asyncio
async def test_improve_runs_six_nodes_and_writes_coverage(
    session: AsyncSession, project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent, neighbor = _cell(project)
    session.add_all([project, parent, neighbor])
    await session.flush()
    prompts: list[str] = []
    pieces = split_vo_for_shots(VO, len(STEPS))

    async def fake_ask(text: str, **kwargs):  # noqa: ANN003
        prompts.append(text)
        if "Агент: биты закадра" in text:
            return _ops(
                {
                    "биты": [
                        {"порядок": 1, "изменение": "он пришёл домой и оставил носок", "якорь": "Он пришёл домой поздно"},
                        {"порядок": 2, "изменение": "девушка зашла, закричала, убежала", "якорь": "Через минуту к нему"},
                    ]
                }
            )
        if "Агент: главное действие" in text:
            chain = "1. дом — " + " → ".join(s[1] for s in STEPS) + f"\n({VO})"
            return _ops(
                {
                    "главное_действие": chain,
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
            return _ops(
                {
                    "кадры": [
                        {
                            "место": p,
                            "действие": a,
                            "роль": r,
                            "объект": o,
                            "план": pl,
                            "ракурс": an,
                            "движение": mv,
                            "стык": st,
                            "закадр": pieces[i],
                        }
                        for i, (p, a, r, o, pl, an, mv, st) in enumerate(STEPS)
                    ]
                }
            )
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
    assert all("cd" * 12 not in p for p in prompts)
    assert "Режиссура" in prompts[1] and "Режиссура" in prompts[2]
    assert "Бюджет: не больше 12" in prompts[1]

    report = result["improve_report"]
    assert [n["node"] for n in report["nodes"]] == [key for key, _ in GROUP_NODES]
    assert result["inserted_frames"] == len(STEPS) - 1
    assert result["images"] == len(STEPS)
    assert "Камера: план ОБЩИЙ" in result["image_ops"][0]["instruction"]
    assert report["passport"]["place"] == "дом"
    assert report["passport"]["sense"].startswith("покой дома")

    frames = list(
        (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.sort_key)
            )
        )
        .scalars()
        .all()
    )
    cell = [fr for fr in frames if fr.uuid == UID or (fr.attrs or {}).get("camera_subdivide", {}).get("parent_uuid") == UID]
    assert len(cell) == len(STEPS)
    assert " ".join(fr.voiceover_text or "" for fr in cell) == " ".join(VO.split())
    cs = [(fr.attrs or {}).get("camera_subdivide") or {} for fr in cell]
    assert [c.get("план") for c in cs] == [s[4] for s in STEPS]
    assert cs[7].get("движение") == "наезд"
    assert cs[1].get("переход") == "cut_on_action"
    assert cs[7].get("роль_кадра") == "реакция"
    head = next(fr for fr in cell if fr.uuid == UID)
    assert (head.attrs or {}).get("смысл_сцены", "").startswith("покой дома")
    assert (head.attrs or {}).get("фон") == "тёмный коридор"
    assert (head.attrs or {}).get(REPORT_ATTR)
    assert neighbor.number == 4
    assert neighbor.voiceover_text.startswith("Утром")


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
