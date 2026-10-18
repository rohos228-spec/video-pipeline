"""Ревью #352: цепь длиннее ячейки — хвост закадра остаётся на последнем кадре."""

from pathlib import Path

import pytest
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
    return Project(id=41, slug="cov-tail", topic="t", hero_mode="auto")


@pytest.mark.asyncio
async def test_grow_false_glues_voiceover_tail(
    session: AsyncSession, project: Project
) -> None:
    parent_uid = "ab" * 12
    parent = Frame(
        project_id=project.id,
        number=7,
        uuid=parent_uid,
        voiceover_text="первый кусок текста второй кусок текста хвост реплики целиком",
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
        {"id": "7-K1", "закадр": "первый кусок текста", "действие": "шаг один видимый", "план": "ОБЩИЙ"},
        {"id": "7-K2", "закадр": "второй кусок текста", "действие": "шаг два видимый", "план": "СРЕДНИЙ"},
        {"id": "7-K3", "закадр": "хвост реплики целиком", "действие": "шаг три видимый", "план": "КРУПНЫЙ"},
    ]
    frames = [parent]
    report = await apply_coverage_scene_action(
        session,
        project,
        parent,
        frames,
        "1. двор — шаг один видимый",
        grow=False,
        kadry=kadry,
    )
    assert report["shots"] == 1
    assert report["skipped_shots"] == 2
    assert report["inserted_frames"] == 0
    assert parent.voiceover_text == (
        "первый кусок текста второй кусок текста хвост реплики целиком"
    )
    stored = parent.attrs.get("кадры") or []
    assert len(stored) == 3
    assert "хвост реплики целиком" in " ".join(
        str(s.get("закадр") or "") for s in stored
    )


@pytest.mark.asyncio
async def test_max_improve_slice_keeps_voiceover_tail(
    session: AsyncSession, project: Project
) -> None:
    parent_uid = "cd" * 12
    pieces = [f"кусок номера {i:02d} остаётся в закадре" for i in range(1, MAX_IMPROVE_SHOTS + 2)]
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
    action = " → ".join(f"видимый шаг номер {i:02d}" for i in range(1, MAX_IMPROVE_SHOTS + 2))
    full = " ".join(pieces)
    await apply_coverage_scene_action(
        session,
        project,
        parent,
        [parent],
        action,
        grow=True,
    )
    rows = list(
        (
            await session.execute(
                __import__("sqlalchemy").select(Frame).where(Frame.project_id == project.id)
            )
        ).scalars()
    )
    assert len(rows) >= 1
    joined = " ".join(
        " ".join(str(fr.voiceover_text or "").split())
        for fr in sorted(rows, key=lambda fr: int(fr.number))
    )
    assert " ".join(joined.split()) == full
