"""Staging-ячейки и хронология scene_design (in-memory SQLite)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project
from app.services.scene_design import cells as sd_cells
from app.services.scene_design import chronology as sd_chronology
from app.services.scene_design import context_builder as sd_context

_VO = (
    "Альфа начало истории. Бета середина пути. Гамма финал рассказа. "
    "Дельта эпилог тихий."
)


def _project() -> SimpleNamespace:
    return SimpleNamespace(id=1)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add(Project(topic="t", slug="cells-test"))
        await s.commit()
        yield s
    await engine.dispose()


# ── slice_to_cells: конвертация и vo_offset ─────────────────────────


def test_action_cells_offsets_and_fields() -> None:
    p = _project()
    slice_data = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "start_words": "Альфа начало",
                "end_words": "середина пути",
                "структура_сцены": "continuity",
                "тип_стыка": "action",
                "переход_в_сцену": "cut",
                "смысл_сцены": "начало",
            },
            {
                "id_scene": "scene_02",
                "start_words": "Гамма финал",
                "end_words": "эпилог тихий",
                "структура_сцены": "associative",
                "тип_стыка": "form",
                "переход_в_сцену": "dissolve",
                "смысл_сцены": "финал",
            },
        ]
    }
    cells = sd_cells.slice_to_cells(p, "action", slice_data, _VO)
    ok = [c for c in cells if c.status == "ok"]
    assert not [c for c in cells if c.status == "rejected"]
    s1 = next(c for c in ok if c.target_key == "scene_01" and c.field == "start_words")
    s2 = next(c for c in ok if c.target_key == "scene_02" and c.field == "start_words")
    assert s1.vo_offset is not None and s2.vo_offset is not None
    assert s1.vo_offset < s2.vo_offset  # хронология вычислима
    assert all(c.agent == "action" and c.kind == "scene" for c in ok)


def test_action_bad_quote_rejected() -> None:
    p = _project()
    slice_data = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "start_words": "нет таких слов в закадре",
                "end_words": "середина пути",
                "структура_сцены": "continuity",
            }
        ]
    }
    cells = sd_cells.slice_to_cells(p, "action", slice_data, _VO)
    rejected = [c for c in cells if c.status == "rejected"]
    assert any(c.field == "start_words" for c in rejected)
    assert any("не найдена" in (c.error or "") for c in rejected)


def test_action_bad_enums_rejected() -> None:
    p = _project()
    slice_data = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "start_words": "Альфа начало",
                "end_words": "середина пути",
                "структура_сцены": "wrong",
                "тип_стыка": "teleport",
                "переход_в_сцену": "beam_up",
            }
        ]
    }
    cells = sd_cells.slice_to_cells(p, "action", slice_data, _VO)
    rejected_fields = {c.field for c in cells if c.status == "rejected"}
    assert {"структура_сцены", "тип_стыка", "переход_в_сцену"} <= rejected_fields


def test_characters_id_pattern() -> None:
    p = _project()
    slice_data = {
        "characters": [
            {"id": "c01", "имя": "Альфа", "внешность": "высокий"},
            {"id": "hero", "имя": "Бета"},
        ]
    }
    cells = sd_cells.slice_to_cells(p, "characters", slice_data, _VO)
    ok_keys = {c.target_key for c in cells if c.status == "ok"}
    rejected_keys = {c.target_key for c in cells if c.status == "rejected"}
    assert "c01" in ok_keys
    assert "hero" in rejected_keys


def test_camera_feature_dictionary() -> None:
    p = _project()
    slice_data = {
        "shot_plan": [
            {
                "цитата": "Бета середина",
                "особенность_сцены": "Крупный план лица",
                "тип_стыка": "action",
            },
            {
                "цитата": "Гамма финал",
                "особенность_сцены": "Красивый план",
            },
        ]
    }
    cells = sd_cells.slice_to_cells(p, "camera", slice_data, _VO)
    shot1 = [c for c in cells if c.target_key == "shot_01"]
    shot2 = [c for c in cells if c.target_key == "shot_02"]
    assert all(c.status == "ok" for c in shot1)
    assert any(
        c.field == "особенность_сцены" and c.status == "rejected" for c in shot2
    )
    q = next(c for c in shot1 if c.field == "цитата")
    assert q.vo_offset is not None


def test_camera_keeps_id_scene_phase_beat() -> None:
    p = _project()
    slice_data = {
        "shot_plan": [
            {
                "id_scene": "scene_01",
                "phase_index": 2,
                "beat": "develop",
                "цитата": "Бета середина",
                "крупность": "MS",
                "кто_в_кадре": "c01",
                "переход": "eyeline",
                "особенность_сцены": "Крупный план лица",
                "тип_стыка": "action",
            }
        ]
    }
    cells = sd_cells.slice_to_cells(p, "camera", slice_data, _VO)
    fields = {c.field: c.value for c in cells if c.status == "ok"}
    assert fields.get("id_scene") == "scene_01"
    assert fields.get("phase_index") == "2"
    assert fields.get("beat") == "develop"
    assert fields.get("кто_в_кадре") == "c01"
    assert fields.get("переход") == "eyeline"


def test_style_hint_offset() -> None:
    p = _project()
    slice_data = {
        "style_arc": [
            {"scene_hint": "Гамма финал", "тип_сцены": "Минимализм", "тон": "тихо"},
            {"scene_hint": "выдуманные слова", "тип_сцены": "Поп-арт"},
        ]
    }
    cells = sd_cells.slice_to_cells(p, "style", slice_data, _VO)
    stage1 = [c for c in cells if c.target_key == "stage_01"]
    assert all(c.status == "ok" for c in stage1)
    assert any(
        c.target_key == "stage_02" and c.status == "rejected" and c.field == "scene_hint"
        for c in cells
    )


# ── store/load/wipe ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_store_load_wipe_chunks(session) -> None:
    project = (await session.execute(__import__("sqlalchemy").select(Project))).scalars().first()
    p = SimpleNamespace(id=project.id)
    slice_data = {
        "characters": [
            {"id": f"c{i:02d}", "имя": f"Персонаж {i}", "внешность": "x"}
            for i in range(1, 8)
        ]
    }
    cells = sd_cells.slice_to_cells(p, "characters", slice_data, _VO)
    stats = await sd_cells.store_cells(session, project, "characters", cells, chunk_size=3)
    assert stats["stored"] == len(cells)
    loaded = await sd_cells.load_cells(session, project)
    assert len(loaded) == len(cells)
    # Перезапись агента не дублирует ячейки (свежие объекты, как в проде).
    cells2 = sd_cells.slice_to_cells(p, "characters", slice_data, _VO)
    await sd_cells.store_cells(session, project, "characters", cells2, chunk_size=3)
    assert len(await sd_cells.load_cells(session, project)) == len(cells)
    deleted = await sd_cells.wipe_cells(session, project)
    assert deleted == len(cells)
    assert await sd_cells.load_cells(session, project) == []


# ── хронология ──────────────────────────────────────────────────────


def _frame(uuid: str, number: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        uuid=uuid, number=number, voiceover_text=text, duration_seconds=3.0
    )


def test_build_assembly_input_chronology() -> None:
    p = _project()
    frames = [
        _frame("u1", 1, "Альфа начало истории."),
        _frame("u2", 2, "Бета середина пути."),
        _frame("u3", 3, "Гамма финал рассказа."),
        _frame("u4", 4, "Дельта эпилог тихий."),
    ]
    action_slice = {
        "scenes": [
            {
                "id_scene": "scene_02",
                "start_words": "Гамма финал",
                "end_words": "эпилог тихий",
                "структура_сцены": "continuity",
            },
            {
                "id_scene": "scene_01",
                "start_words": "Альфа начало",
                "end_words": "середина пути",
                "структура_сцены": "continuity",
            },
        ]
    }
    cells = sd_cells.slice_to_cells(p, "action", action_slice, _VO)
    payload = sd_chronology.build_assembly_input(p, frames, cells, _VO)

    scenes = payload["scenes_chrono"]
    # scene_01 идёт первой, хотя в срезе была второй.
    assert [s["id_scene"] for s in scenes] == ["scene_01", "scene_02"]
    assert [f["uuid"] for f in scenes[0]["кадры"]] == ["u1", "u2"]
    assert [f["uuid"] for f in scenes[1]["кадры"]] == ["u3", "u4"]
    # Хронометраж: у кадров время из БД (3.0), у сцены — сумма её кадров.
    assert scenes[0]["кадры"][0]["время_сек"] == 3.0
    assert scenes[0]["время_сек"] == 6.0
    assert scenes[0]["кадров"] == 2
    assert scenes[1]["время_сек"] == 6.0


def test_action_scene_time_cell_passes() -> None:
    p = _project()
    slice_data = {
        "scenes": [
            {
                "id_scene": "scene_01",
                "start_words": "Альфа начало",
                "end_words": "середина пути",
                "время_сек": 6.0,
                "структура_сцены": "continuity",
            }
        ]
    }
    cells = sd_cells.slice_to_cells(p, "action", slice_data, _VO)
    time_cell = next(
        (c for c in cells if c.field == "время_сек" and c.status == "ok"), None
    )
    assert time_cell is not None
    assert time_cell.value == "6.0"


# ── хронометраж кадров (context_builder.frame_seconds) ──────────────


def test_frame_seconds_vo_beats_stale_db() -> None:
    # БД часто врёт (~2с); SoT — len(VO)/14
    fr = SimpleNamespace(duration_seconds=2.0, voiceover_text="x" * 140)
    assert sd_context.frame_seconds(fr) == (10.0, "vo_14cps")


def test_frame_seconds_estimate_from_voiceover() -> None:
    fr = SimpleNamespace(duration_seconds=None, voiceover_text="а" * 28)
    sec, source = sd_context.frame_seconds(fr)
    assert source == "vo_14cps"
    assert sec == 2.0  # 28 символов / 14 сим/сек


def test_shared_context_has_frame_times_and_total() -> None:
    p = SimpleNamespace(
        script_text="", general_plan="", meta={},
    )
    frames = [
        _frame("u1", 1, "Альфа начало истории."),  # 21 сим → 1.5с
        SimpleNamespace(uuid="u2", number=2, voiceover_text="а" * 28,
                        duration_seconds=2.0),  # БД игнор → 2.0 vo
    ]
    ctx = sd_context.build_shared_context(p, frames)
    assert '"время_источник":"vo_14cps"' in ctx
    assert "время_бд_сек" not in ctx
    assert "ИТОГО: 3.5 сек" in ctx


def test_build_assembly_input_unassigned_frames_kept() -> None:
    p = _project()
    frames = [_frame("u1", 1, "Альфа начало истории.")]
    payload = sd_chronology.build_assembly_input(p, frames, [], _VO)
    assert payload["scenes_chrono"] == []
    assert [f["uuid"] for f in payload["кадры_без_диапазона"]] == ["u1"]


def test_frame_offsets_sequential_for_repeats() -> None:
    vo = "Тишина. Шаг вперёд. Тишина. Остановка."
    frames = [
        _frame("u1", 1, "Тишина."),
        _frame("u2", 2, "Шаг вперёд."),
        _frame("u3", 3, "Тишина."),
    ]
    offsets = sd_chronology.frame_offsets(frames, vo)
    assert offsets["u1"] is not None and offsets["u3"] is not None
    assert offsets["u1"] < offsets["u3"]  # второе «Тишина» — не первое вхождение
