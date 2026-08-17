"""Волна 0: черновик → проверки → редактор скелета."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus
from app.services import gpt_client
from app.services.scene_design import cells as sd_cells
from app.services.scene_design import runner
from app.services.scene_design import skeleton as sk


def _fr(n: int, text: str) -> Frame:
    return Frame(
        project_id=1,
        number=n,
        voiceover_text=text,
        status="planned",
        uuid=f"u{n:03d}",
    )


def _draft_ok(frames_text: list[tuple[int, str]]) -> dict:
    scenes = []
    for i, (n, t) in enumerate(frames_text, 1):
        scenes.append(
            {
                "id_scene": f"scene_{i:02d}",
                "кадры": [n],
                "связь_с_прошлой": {
                    "тип": "начало" if i == 1 else "продолжение",
                    "что_связывает": "мастерская",
                },
                "суть": t,
                "главное": {
                    "тип": "предмет",
                    "что": "часы",
                    "якорь_в_кадрах": t[:48],
                    "нить": "",
                },
                "биты": [],
                "персонажи": [{"id": "c01", "как": "действует"}],
                "место_id": "loc01",
                "время": "утро",
                "длительность_сек": round(len(t) / 14.0, 1),
            }
        )
    return {
        "scenes": scenes,
        "characters_seed": [
            {"id": "c01", "имя": "Павел", "якорь": "Павел", "вне_кадра": False}
        ],
        "locations_seed": [
            {"id": "loc01", "name": "мастерская", "якорь": "мастерскую"}
        ],
        "report": "ok",
    }


@pytest.fixture
async def sk_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "scene_design_enabled", True)
    monkeypatch.setattr(settings, "scene_design_skeleton_enabled", True)
    monkeypatch.setattr(settings, "scene_design_vo_chars_per_sec", 14.0)
    monkeypatch.setattr(settings, "subtitle_chars_per_second", 14.0)

    (tmp_path / "videos" / "sk").mkdir(parents=True)
    db = tmp_path / "sk.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    texts = [
        (1, "Павел открыл мастерскую у моста"),
        (2, "В мастерской он взял сломанные часы с боем"),
        (3, "и решил их починить во что бы то ни стало"),
    ]
    async with factory() as session:
        p = Project(
            topic="sk",
            slug="sk",
            status=ProjectStatus.scene_designing,
            script_text="Павел в мастерской чинит часы. Потом уходит.",
            meta={"scene_design_enabled": True, "scene_design_skeleton": True},
        )
        session.add(p)
        await session.flush()
        for n, t in texts:
            session.add(
                Frame(
                    project_id=p.id,
                    number=n,
                    voiceover_text=t,
                    status="planned",
                    uuid=f"u{n:03d}",
                )
            )
        await session.commit()
        yield session, p, texts


# ── unit validate_skeleton ───────────────────────────────────────────


def test_glue_vo_cells_is_gap() -> None:
    frames = [_fr(1, "один"), _fr(2, "два")]
    draft = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "кадры": [1, 2],
                "связь_с_прошлой": {"тип": "начало"},
                "главное": {},
                "биты": [],
                "персонажи": [],
            }
        ],
        "characters_seed": [],
        "locations_seed": [],
    }
    gaps = sk.validate_skeleton(draft, frames, "один два")
    assert any("склейка" in g["проблема"] for g in gaps)


def test_explode_glued_vo_scenes() -> None:
    frames = [_fr(1, "Павел открыл мастерскую у моста"), _fr(2, "взял сломанные часы")]
    draft = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "кадры": [1, 2],
                "связь_с_прошлой": {"тип": "начало", "что_связывает": ""},
                "суть": "склейка",
                "главное": {"тип": "предмет", "что": "часы", "якорь_в_кадрах": "мастерскую"},
                "биты": [],
                "персонажи": [{"id": "c01", "как": "действует"}],
                "место_id": "loc01",
                "время": "утро",
                "длительность_сек": 9.0,
            }
        ],
        "characters_seed": [{"id": "c01", "имя": "Павел", "якорь": "Павел"}],
        "locations_seed": [{"id": "loc01", "name": "мастерская", "якорь": "мастерскую"}],
    }
    n = sk.explode_glued_vo_scenes(draft, frames)
    assert n == 1
    assert [s["кадры"] for s in draft["scenes"]] == [[1], [2]]
    assert draft["scenes"][0]["id_scene"] == "scene_01"
    assert draft["scenes"][1]["id_scene"] == "scene_02"
    assert draft["scenes"][1]["связь_с_прошлой"]["тип"] != "начало"
    glue = [g for g in sk.validate_skeleton(draft, frames, "Павел открыл мастерскую у моста взял сломанные часы") if "склейка" in g["проблема"]]
    assert glue == []


def test_coverage_missing_frame() -> None:
    frames = [_fr(1, "один"), _fr(2, "два"), _fr(3, "три")]
    draft = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "кадры": [1, 2],
                "связь_с_прошлой": {"тип": "начало"},
                "главное": {},
                "биты": [],
                "персонажи": [],
            }
        ],
        "characters_seed": [],
        "locations_seed": [],
    }
    gaps = sk.validate_skeleton(draft, frames, "один два три")
    assert any("не покрыты" in g["проблема"] for g in gaps)


def test_heal_open_threads_continues_in_last_scene() -> None:
    frames = [_fr(1, "семья в квартире"), _fr(2, "суд вынес приговор")]
    draft = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "кадры": [1],
                "связь_с_прошлой": {"тип": "начало"},
                "главное": {"нить": "семья спесивцевых", "изменение": "показали"},
                "нити": [{"имя": "семья спесивцевых", "статус": "открыта"}],
                "биты": [],
                "персонажи": [],
                "место_id": "loc01",
            },
            {
                "id_scene": "scene_02",
                "кадры": [2],
                "связь_с_прошлой": {"тип": "новое_место"},
                "главное": {},
                "нити": [],
                "биты": [],
                "персонажи": [],
                "место_id": "loc02",
            },
        ],
        "characters_seed": [],
        "locations_seed": [
            {"id": "loc01", "name": "квартира", "якорь": "квартире"},
            {"id": "loc02", "name": "суд", "якорь": "суд"},
        ],
    }
    before = sk.validate_skeleton(draft, frames, "семья в квартире суд вынес приговор")
    assert any("не закрыта" in g["проблема"] for g in before)
    assert sk.heal_open_threads(draft) == 1
    after = sk.validate_skeleton(draft, frames, "семья в квартире суд вынес приговор")
    assert not any("не закрыта" in g["проблема"] for g in after)


def test_coverage_non_adjacent() -> None:
    frames = [_fr(1, "а"), _fr(2, "б"), _fr(3, "в")]
    draft = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "кадры": [1, 3],
                "связь_с_прошлой": {"тип": "начало"},
                "главное": {},
                "биты": [],
                "персонажи": [],
            }
        ],
        "characters_seed": [],
        "locations_seed": [],
    }
    gaps = sk.validate_skeleton(draft, frames, "а б в")
    assert any("сосед" in g["проблема"] for g in gaps)


def test_link_type_continuation_vs_new_place() -> None:
    frames = [
        _fr(1, "Павел в мастерской"),
        _fr(2, "Потом он вышел на улицу к реке"),
    ]
    draft = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "кадры": [1],
                "связь_с_прошлой": {"тип": "начало"},
                "главное": {"якорь_в_кадрах": "в мастерской"},
                "биты": [],
                "персонажи": [],
                "место_id": "loc01",
                "длительность_сек": 1.5,
            },
            {
                "id_scene": "scene_02",
                "кадры": [2],
                "связь_с_прошлой": {
                    "тип": "продолжение",
                    "что_связывает": "loc01",
                },
                "главное": {"якорь_в_кадрах": "на улицу"},
                "биты": [],
                "персонажи": [],
                "место_id": "loc02",
                "длительность_сек": 2.0,
            },
        ],
        "characters_seed": [],
        "locations_seed": [
            {"id": "loc01", "name": "мастерская", "якорь": "мастерской"},
            {"id": "loc02", "name": "улица", "якорь": "улицу"},
        ],
    }
    gaps = sk.validate_skeleton(
        draft, frames, "Павел в мастерской Потом он вышел на улицу к реке"
    )
    assert any(
        "ожидается" in g["проблема"] and "новое_место" in g["проблема"]
        for g in gaps
    )


def test_timing_gap() -> None:
    long = ("слово " * 80).strip()
    frames = [_fr(1, long)]
    draft = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "кадры": [1],
                "связь_с_прошлой": {"тип": "начало"},
                "главное": {"якорь_в_кадрах": "слово"},
                "биты": [],
                "персонажи": [],
                "длительность_сек": 2.0,
            }
        ],
        "characters_seed": [],
        "locations_seed": [],
    }
    gaps = sk.validate_skeleton(draft, frames, long)
    assert any("длительность" in g["адрес"] for g in gaps)


def test_anchor_not_in_vo() -> None:
    frames = [_fr(1, "Павел чинит часы")]
    draft = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "кадры": [1],
                "связь_с_прошлой": {"тип": "начало"},
                "главное": {"якорь_в_кадрах": "летающая тарелка"},
                "биты": [],
                "персонажи": [],
            }
        ],
        "characters_seed": [],
        "locations_seed": [],
    }
    gaps = sk.validate_skeleton(draft, frames, "Павел чинит часы")
    assert any("якорь" in g["проблема"] for g in gaps)


def test_skeleton_vo_frames_skips_empty() -> None:
    frames = [
        _fr(1, "есть текст"),
        _fr(2, ""),
        _fr(3, "   "),
        _fr(4, "ещё текст"),
    ]
    vos = sk.skeleton_vo_frames(frames)
    assert [int(f.number) for f in vos] == [1, 4]


def test_timing_gap_skipped_for_empty_vo() -> None:
    """Пустой закадр + duration из БД не должен давать тайминг-разрыв."""
    frames = [_fr(1, "Павел открыл мастерскую"), _fr(2, "")]
    frames[1].duration_seconds = 2.5
    draft = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "кадры": [1],
                "связь_с_прошлой": {"тип": "начало"},
                "суть": "открыл",
                "главное": {
                    "тип": "действие",
                    "глагол": "открыл",
                    "якорь_в_кадрах": "открыл мастерскую",
                },
                "биты": [],
                "персонажи": [],
                "длительность_сек": round(len("Павел открыл мастерскую") / 14.0, 1),
            },
            {
                "id_scene": "scene_02",
                "кадры": [2],
                "связь_с_прошлой": {"тип": "продолжение"},
                "суть": "",
                "главное": {},
                "биты": [],
                "персонажи": [],
                "длительность_сек": 2.5,
            },
        ],
        "characters_seed": [],
        "locations_seed": [],
    }
    gaps = sk.validate_skeleton(draft, frames, "Павел открыл мастерскую")
    assert not any("тайминг" in g["проблема"] for g in gaps)


def test_normalize_cells_to_scenes() -> None:
    draft = {
        "cells": [
            {
                "id_cell": "cell_01",
                "кадр": 1,
                "связь_с_прошлой": {"тип": "начало", "что_связывает": ""},
                "фокус": "Уилер входит без маски",
                "смысловые_части": [
                    {
                        "порядок": 1,
                        "тип": "действие",
                        "глагол_или_суть": "вошёл",
                        "изменение": "снаружи → внутри",
                        "якорь": "вошёл в два банка",
                        "нить": "p01",
                        "главный": True,
                    }
                ],
                "предметы": [{"id": "p01", "как": "держится"}],
                "персонажи": [{"id": "c01", "как": "действует"}],
                "место_id": "loc01",
                "длительность_сек": 5.0,
            }
        ],
        "items_seed": [{"id": "p01", "имя": "лимонный сок", "якорь": "лимонный сок"}],
        "characters_seed": [{"id": "c01", "имя": "Уилер", "якорь": "Уилер"}],
        "locations_seed": [{"id": "loc01", "name": "банк", "якорь": "банка"}],
    }
    sk.normalize_skeleton_draft(draft)
    assert len(draft["scenes"]) == 1
    sc = draft["scenes"][0]
    assert sc["id_scene"] == "scene_01"
    assert sc["кадры"] == [1]
    assert sc["суть"] == "Уилер входит без маски"
    assert sc["биты"][0]["глагол"] == "вошёл"
    assert sc["главное"]["якорь_в_кадрах"] == "вошёл в два банка"
    assert sc["предметы"][0]["id"] == "p01"


def test_merge_by_id() -> None:
    draft = {
        "scenes": [
            {"id_scene": "scene_01", "кадры": [1], "суть": "old"},
            {"id_scene": "scene_02", "кадры": [2], "суть": "keep"},
        ],
        "characters_seed": [{"id": "c01", "имя": "A"}],
        "locations_seed": [
            {"id": "loc01", "name": "X"},
            {"id": "loc02", "name": "Y-dup"},
        ],
    }
    editor = {
        "fixed_scenes": [{"id_scene": "scene_01", "кадры": [1], "суть": "new"}],
        "fixed_characters": [],
        "fixed_locations": [{"id": "loc01", "name": "Y"}],
    }
    out = sk.merge_by_id(draft, editor)
    assert out["scenes"][0]["суть"] == "new"
    assert out["scenes"][1]["суть"] == "keep"
    assert out["locations_seed"][0]["name"] == "Y"
    assert len(out["locations_seed"]) == 1


# ── integration with mocked GPT ──────────────────────────────────────


@pytest.mark.asyncio
async def test_run_skeleton_clean_draft_one_call(sk_session, monkeypatch):
    session, project, texts = sk_session
    calls: list[str] = []
    draft = _draft_ok(texts)

    async def fake_ask(text, **kwargs):
        calls.append(text)
        assert "СКЕЛЕТ" in text and "ЧЕРНОВИК" in text
        assert "РЕДАКТОР" not in text.split("---")[0]
        return json.dumps(draft, ensure_ascii=False)

    monkeypatch.setattr(gpt_client, "gpt_ask_fresh", fake_ask)
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    p = await session.get(Project, project.id)
    result = await sk.run_skeleton(session, p, list(frames))
    assert len(calls) == 1
    assert result["scenes"][0]["id_scene"] == "scene_01"
    cells = await sd_cells.load_cells(session, p)
    assert any(c.kind == "sk_scene" for c in cells)


@pytest.mark.asyncio
async def test_run_skeleton_editor_fixes_dup_loc(sk_session, monkeypatch):
    session, project, texts = sk_session
    calls: list[str] = []
    bad = _draft_ok(texts)
    bad["locations_seed"] = [
        {"id": "loc01", "name": "мастерская у моста", "якорь": "мастерскую"},
        {"id": "loc02", "name": "мастерская", "якорь": "мастерскую"},
    ]

    async def fake_ask(text, **kwargs):
        calls.append(text)
        if "РЕДАКТОР" in text:
            return json.dumps(
                {
                    "fixed_scenes": [],
                    "fixed_characters": [],
                    "fixed_locations": [bad["locations_seed"][0]],
                    "report": "слил loc02 в loc01",
                },
                ensure_ascii=False,
            )
        return json.dumps(bad, ensure_ascii=False)

    monkeypatch.setattr(gpt_client, "gpt_ask_fresh", fake_ask)
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    p = await session.get(Project, project.id)
    result = await sk.run_skeleton(session, p, list(frames))
    assert len(calls) == 2
    assert len(result["locations_seed"]) == 1


@pytest.mark.asyncio
async def test_run_skeleton_editor_fail_twice(sk_session, monkeypatch):
    session, project, texts = sk_session
    bad = _draft_ok(texts)
    bad["scenes"][0]["главное"]["якорь_в_кадрах"] = "XXX_NOT_IN_VO_YYY"

    async def fake_ask(text, **kwargs):
        if "РЕДАКТОР" in text:
            return json.dumps(
                {
                    "fixed_scenes": bad["scenes"],
                    "fixed_characters": [],
                    "fixed_locations": [],
                    "report": "не смог",
                }
            )
        return json.dumps(bad)

    monkeypatch.setattr(gpt_client, "gpt_ask_fresh", fake_ask)
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    p = await session.get(Project, project.id)
    with pytest.raises(RuntimeError, match="разрывы не закрыты"):
        await sk.run_skeleton(session, p, list(frames))


@pytest.mark.asyncio
async def test_run_skeleton_checkpoint_skips_gpt(sk_session, monkeypatch):
    session, project, texts = sk_session
    draft = _draft_ok(texts)
    runner.save_checkpoint(project, "skeleton", draft)

    async def boom(*a, **k):
        raise AssertionError("GPT не должен вызываться")

    monkeypatch.setattr(gpt_client, "gpt_ask_fresh", boom)
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    p = await session.get(Project, project.id)
    out = await sk.run_skeleton(session, p, list(frames))
    assert out["scenes"][0]["id_scene"] == "scene_01"


# ── слоты: 1 бит = 1 слот ────────────────────────────────────────────


def _cell_with_bits() -> dict:
    return {
        "id_cell": "cell_01",
        "кадр": 1,
        "связь_с_прошлой": {"тип": "начало", "что_связывает": ""},
        "фокус": "Уилер входит без маски",
        "смысловые_части": [
            {
                "порядок": 1,
                "тип": "действие",
                "глагол_или_суть": "вошёл",
                "изменение": "снаружи → внутри",
                "якорь": "вошёл в два банка",
                "главный": False,
            },
            {
                "порядок": 2,
                "тип": "состояние",
                "глагол_или_суть": "верит в сок",
                "изменение": "сок для чернил → сок скроет лицо",
                "якорь": "лимонный сок способен скрыть лицо от камер наблюдения",
                "главный": True,
            },
        ],
        "длительность_сек": 12.0,
    }


def test_slots_synthesized_one_per_bit() -> None:
    draft = {"cells": [_cell_with_bits()]}
    sk.normalize_skeleton_draft(draft)
    sc = draft["scenes"][0]
    slots = sc["слоты"]
    assert len(slots) == 2
    assert [s["бит"] for s in slots] == [1, 2]
    assert slots[0]["главный"] is False and slots[1]["главный"] is True
    assert abs(sum(s["длительность_сек"] for s in slots) - 12.0) < 0.01
    # длительность ∝ длине якоря: второй якорь длиннее → слот длиннее
    assert slots[1]["длительность_сек"] > slots[0]["длительность_сек"]
    # слоты видны и в карточке cells
    assert draft["cells"][0]["слоты"] == slots
    sk.validate_skeleton_slots(draft["scenes"])  # не падает


def test_slots_from_model_carried_asis() -> None:
    cell = _cell_with_bits()
    cell["слоты"] = [
        {"слот": 1, "бит": 1, "якорь": "вошёл в два банка",
         "фокус_слота": "вход", "длительность_сек": 5.0, "главный": False},
        {"слот": 2, "бит": 2, "якорь": "лимонный сок",
         "фокус_слота": "вера", "длительность_сек": 7.0, "главный": True},
    ]
    draft = {"cells": [cell]}
    sk.normalize_skeleton_draft(draft)
    sc = draft["scenes"][0]
    assert sc["слоты"][1]["фокус_слота"] == "вера"
    sk.validate_skeleton_slots(draft["scenes"])


def test_validate_slots_count_mismatch() -> None:
    sc = {
        "id_scene": "scene_01",
        "биты": [{"порядок": 1}, {"порядок": 2}],
        "слоты": [{"слот": 1, "якорь": "x", "главный": True}],
        "длительность_сек": 5.0,
    }
    with pytest.raises(Exception, match="слотов 1 != битов 2"):
        sk.validate_skeleton_slots([sc])


def test_validate_slots_two_main() -> None:
    sc = {
        "id_scene": "scene_01",
        "биты": [{"порядок": 1}, {"порядок": 2}],
        "слоты": [
            {"слот": 1, "якорь": "a", "главный": True},
            {"слот": 2, "якорь": "b", "главный": True},
        ],
    }
    with pytest.raises(Exception, match="главных слотов 2"):
        sk.validate_skeleton_slots([sc])


def test_validate_slots_duration_mismatch() -> None:
    sc = {
        "id_scene": "scene_01",
        "биты": [{"порядок": 1}],
        "слоты": [{"слот": 1, "якорь": "a", "главный": True, "длительность_сек": 2.0}],
        "длительность_сек": 10.0,
    }
    with pytest.raises(Exception, match="сумма слотов"):
        sk.validate_skeleton_slots([sc])


def test_validate_slots_thesis_cell_without_bits_ok() -> None:
    sk.validate_skeleton_slots([{"id_scene": "scene_01", "биты": [], "слоты": []}])


@pytest.mark.asyncio
async def test_slots_stored_as_sk_slot_cells(sk_session) -> None:
    session, project, texts = sk_session
    draft = {"cells": [_cell_with_bits()]}
    await sk.store_skeleton_cells(
        session, project, draft, " ".join(t for _, t in texts)
    )
    from app.models import SceneDesignCell

    rows = list(
        (
            await session.execute(
                select(SceneDesignCell).where(SceneDesignCell.kind == "sk_slot")
            )
        ).scalars()
    )
    assert rows, "sk_slot ячейки не записаны"
    keys = {(r.target_key, r.field) for r in rows}
    assert ("scene_01.slot1", "якорь") in keys
    assert ("scene_01.slot2", "главный") in keys
