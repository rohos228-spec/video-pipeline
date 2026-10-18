"""Рендер не раскладывает неактивные шоты. кадры[] хранит их целиком."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from loguru import logger


@pytest.fixture
def warn_log():
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


def test_camera_chips_ignore_inactive_shot_id() -> None:
    """camera_chips_from_frame: id неактивного шота не отдаёт его план."""
    from app.services.db_frames_context import camera_chips_from_frame

    frame = SimpleNamespace(
        attrs={
            "кадры": [
                {
                    "id": "dead",
                    "план": "КРУПНЫЙ",
                    "ракурс": "макро",
                    "движение": "наезд",
                    "активен": False,
                },
                {"id": "live", "план": "ОБЩИЙ", "ракурс": "фронт", "движение": "статика"},
            ],
            "camera_subdivide": {"shot_id": "dead"},
        }
    )
    chips = camera_chips_from_frame(frame)
    assert chips == {"план": "", "ракурс": "", "движение": ""}
    assert len(frame.attrs["кадры"]) == 2

    only = SimpleNamespace(
        attrs={
            "кадры": [{"id": "dead", "план": "КРУПНЫЙ", "удалён": True}],
            "camera_subdivide": {},
        }
    )
    assert camera_chips_from_frame(only)["план"] == ""


def test_slim_img_pr_row_skips_inactive_shot_for_the_model() -> None:
    """Модели не показываем неактивный шот ни по id, ни как первый кадр."""
    from app.services.db_frames_context import _slim_img_pr_frame_row

    stored = [
        {"id": "dead", "план": "КРУПНЫЙ", "действие": "чужое", "активен": False},
        {"id": "live", "план": "ОБЩИЙ", "действие": "живое"},
    ]
    original = [dict(shot) for shot in stored]
    row = {"shot_id": "dead", "кадры": stored}
    _slim_img_pr_frame_row(row)
    assert "кадры" not in row
    assert stored[0]["действие"] == "чужое"
    assert stored[0]["активен"] is False

    row_first = {"кадры": [dict(shot) for shot in original]}
    _slim_img_pr_frame_row(row_first)
    assert row_first["кадры"] == [{"id": "live", "план": "ОБЩИЙ", "действие": "живое"}]
    assert original[0]["активен"] is False


def test_slim_img_pr_row_does_not_copy_inactive_shot() -> None:
    """_slim_img_pr_frame_row: выбор по id не берёт неактивный шот."""
    from app.services.db_frames_context import _slim_img_pr_frame_row

    stored = [
        {"id": "dead", "план": "КРУПНЫЙ", "действие": "чужое", "активен": False},
        {"id": "live", "план": "ОБЩИЙ", "действие": "живое"},
    ]
    row = {"shot_id": "dead", "кадры": stored}
    _slim_img_pr_frame_row(row)
    assert "кадры" not in row
    assert stored[0]["действие"] == "чужое"

    row_live = {"shot_id": "live", "кадры": [dict(s) for s in stored]}
    _slim_img_pr_frame_row(row_live)
    assert row_live["кадры"] == [{"id": "live", "план": "ОБЩИЙ", "действие": "живое"}]


@pytest.mark.asyncio
async def test_montage_empty_when_no_live_shots(warn_log) -> None:
    """apply_coverage_scene_action: ноль живых — пустая раскладка и WARN."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Frame, Project
    from app.services.montage_coverage_ops import apply_coverage_scene_action

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = Project(slug="montage-dead", topic="t", meta={})
        session.add(project)
        await session.flush()
        parent = Frame(
            project_id=project.id,
            number=3,
            uuid="ab" * 12,
            voiceover_text="НЕ ТРОГАТЬ",
            status="planned",
            sort_key=3.0,
            attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": "ab" * 12}},
        )
        session.add(parent)
        await session.flush()
        kadry = [{
            "id": "3-K9",
            "действие": "МЁРТВЫЙ ШАГ",
            "план": "КРУПНЫЙ",
            "закадр": "Чужой закадр.",
            "активен": False,
        }]
        report = await apply_coverage_scene_action(
            session,
            project,
            parent,
            [parent],
            "стоит у двери",
            kadry=kadry,
            lock_parent_plan=False,
        )
        assert report["shots"] == 0
        assert report["chain"] == ""
        assert parent.voiceover_text == "НЕ ТРОГАТЬ"
        stored = (parent.attrs or {}).get("кадры") or []
        assert len(stored) == 1
        assert stored[0]["id"] == "3-K9"
        assert stored[0]["закадр"] == "Чужой закадр."
        assert stored[0]["активен"] is False
    await engine.dispose()
    assert any("нет живых шотов" in line for line in warn_log)


@pytest.mark.asyncio
async def test_montage_chain_and_count_use_live_shots() -> None:
    """Цепь и счётчик раскладки не включают неактивный шот."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Frame, Project
    from app.services.montage_coverage_ops import apply_coverage_scene_action

    info: list[str] = []
    sink = logger.add(lambda message: info.append(str(message)), level="INFO")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            project = Project(slug="montage-live", topic="t", meta={})
            session.add(project)
            await session.flush()
            parent = Frame(
                project_id=project.id,
                number=4,
                uuid="cd" * 12,
                voiceover_text="было",
                status="planned",
                sort_key=4.0,
                attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": "cd" * 12}},
            )
            session.add(parent)
            await session.flush()
            kadry = [
                {
                    "id": "4-K1",
                    "действие": "живой шаг",
                    "план": "ОБЩИЙ",
                    "закадр": "Живой закадр кадра.",
                    "место": "архив",
                },
                {
                    "id": "4-K2",
                    "действие": "второй живой",
                    "план": "СРЕДНИЙ",
                    "закадр": "Второй кусок.",
                    "место": "архив",
                },
                {
                    "id": "4-K9",
                    "действие": "МЁРТВЫЙ ШАГ",
                    "план": "КРУПНЫЙ",
                    "закадр": "Чужой закадр.",
                    "место": "архив",
                    "удалён": True,
                },
            ]
            report = await apply_coverage_scene_action(
                session,
                project,
                parent,
                [parent],
                "стоит у двери",
                kadry=kadry,
                lock_parent_plan=False,
            )
            assert "МЁРТВЫЙ" not in report["chain"]
            assert "живой шаг" in report["chain"]
            assert report["shots"] == 1
            assert parent.voiceover_text == "Живой закадр кадра."
            stored = (parent.attrs or {}).get("кадры") or []
            assert [s["id"] for s in stored] == ["4-K1", "4-K2", "4-K9"]
            assert stored[2]["закадр"] == "Чужой закадр."
            assert any("цепь=2 кадров" in line for line in info)
            assert not any("цепь=3 кадров" in line for line in info)
    finally:
        logger.remove(sink)
        await engine.dispose()
