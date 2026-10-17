"""Ревью #352 D: attrs хранит полный кадры[], на кадр — только живые шоты."""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.montage_coverage_ops import (
    MAX_IMPROVE_SHOTS,
    apply_coverage_scene_action,
)


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
    return Project(id=41, slug="cov-d", topic="t", hero_mode="auto")


@pytest.mark.asyncio
async def test_marked_shot_vo_does_not_land_on_live_frame(
    session: AsyncSession, project: Project
) -> None:
    parent_uid = "ab" * 12
    marked = "ПОМЕЧЕННЫЙ закадр не садится на живой"
    parent = Frame(
        project_id=project.id,
        number=7,
        uuid=parent_uid,
        voiceover_text="живой первый кусок текста живой хвост остаётся",
        status="planned",
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
            },
        },
    )
    session.add_all([project, parent])
    await session.flush()
    kadry = [
        {"id": "7-K1", "закадр": "живой первый кусок текста", "действие": "шаг один", "план": "ОБЩИЙ"},
        {
            "id": "7-K2",
            "закадр": marked,
            "действие": "помеченный шаг",
            "план": "ДЕТАЛЬ",
            "активен": False,
        },
        {"id": "7-K3", "закадр": "живой хвост остаётся", "действие": "шаг три", "план": "КРУПНЫЙ"},
    ]
    report = await apply_coverage_scene_action(
        session,
        project,
        parent,
        [parent],
        "1. двор — шаг один",
        grow=False,
        kadry=kadry,
    )
    assert report["shots"] == 1
    assert report["skipped_shots"] == 2
    assert marked not in (parent.voiceover_text or "")
    assert "живой первый кусок текста" in (parent.voiceover_text or "")
    assert "живой хвост остаётся" in (parent.voiceover_text or "")
    stored = parent.attrs.get("кадры") or []
    assert [s["id"] for s in stored] == ["7-K1", "7-K2", "7-K3"]
    kept = next(s for s in stored if s["id"] == "7-K2")
    assert kept["закадр"] == marked
    assert kept.get("активен") is False


@pytest.mark.asyncio
async def test_max_improve_fold_is_not_stored_in_attrs(
    session: AsyncSession, project: Project
) -> None:
    parent_uid = "cd" * 12
    pieces = [
        f"кусок номера {i:02d} остаётся в закадре"
        for i in range(1, MAX_IMPROVE_SHOTS + 2)
    ]
    parent = Frame(
        project_id=project.id,
        number=3,
        uuid=parent_uid,
        voiceover_text=" ".join(pieces),
        status="planned",
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": parent_uid,
            },
        },
    )
    session.add_all([project, parent])
    await session.flush()
    action = " → ".join(
        f"видимый шаг номер {i:02d}" for i in range(1, MAX_IMPROVE_SHOTS + 2)
    )
    await apply_coverage_scene_action(
        session,
        project,
        parent,
        [parent],
        action,
        grow=True,
    )
    row = (
        await session.execute(select(Frame).where(Frame.uuid == parent_uid))
    ).scalar_one()
    stored = list((row.attrs or {}).get("кадры") or [])
    assert len(stored) == MAX_IMPROVE_SHOTS + 1
    assert stored[-1]["id"].endswith(f"K{MAX_IMPROVE_SHOTS + 1}")
    assert any(pieces[-1] in str(s.get("закадр") or "") for s in stored)
