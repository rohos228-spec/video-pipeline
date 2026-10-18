"""Повторное ревью #352 №9: якоря не сдвигают номера старых кадров.

Дефолт ``_grow_cell_shots(..., renumber=True)`` не меняем.
``apply_coverage_anchors`` передаёт ``renumber=False`` явно.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services import db_v2
from app.services.animation_prompt_gpt import scene_image_path
from app.services.montage_coverage_ops import _grow_cell_shots, apply_coverage_anchors

VO = "Тед Банди родился в Берлингтоне. Детство прошло у деда."


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'anchors.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    return Project(id=81, slug="anchors-keep", topic="t", hero_mode="auto")


def test_grow_cell_shots_default_still_renumbers() -> None:
    default = inspect.signature(_grow_cell_shots).parameters["renumber"].default
    assert default is True
    src = inspect.getsource(apply_coverage_anchors)
    assert "renumber=False" in src


@pytest.mark.asyncio
async def test_anchors_keep_old_numbers_and_frame_png(
    session: AsyncSession, project: Project
) -> None:
    session.add(project)
    parent_uid = "aa" * 12
    later_uid = "bb" * 12
    parent = Frame(
        project_id=project.id,
        number=4,
        uuid=parent_uid,
        voiceover_text=VO,
        sort_key=40.0,
        status="planned",
        attrs={
            "camera_subdivide": {"role": "vo_parent", "parent_uuid": parent_uid},
            "vo_cell_full": VO,
        },
    )
    later = Frame(
        project_id=project.id,
        number=9,
        uuid=later_uid,
        voiceover_text="Отдельная ячейка дальше по ленте.",
        sort_key=90.0,
        status="planned",
        attrs={"camera_subdivide": {"role": "vo_parent", "parent_uuid": later_uid}},
    )
    session.add_all([parent, later])
    await session.flush()

    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True)
    preview_parent = scenes / "frame_004_abcd.png"
    preview_later = scenes / "frame_009_wxyz.png"
    preview_parent.write_bytes(b"png-4")
    preview_later.write_bytes(b"png-9")

    frames = list(
        (
            await session.execute(select(Frame).where(Frame.project_id == project.id))
        ).scalars()
    )
    await apply_coverage_anchors(
        session,
        project,
        parent,
        frames,
        [{"якорь": "Тед Банди родился"}, {"якорь": "Детство прошло у деда"}],
    )
    await session.flush()

    rows = list(
        (
            await session.execute(select(Frame).where(Frame.project_id == project.id))
        ).scalars()
    )
    by_uuid = {fr.uuid: int(fr.number) for fr in rows}
    assert by_uuid[parent_uid] == 4
    assert by_uuid[later_uid] == 9
    assert len(rows) == 3
    new_numbers = [n for uid, n in by_uuid.items() if uid not in {parent_uid, later_uid}]
    assert new_numbers == [10]
    assert scene_image_path(project, 4) == preview_parent
    assert scene_image_path(project, 9) == preview_later
    assert preview_parent.read_bytes() == b"png-4"
    assert preview_later.read_bytes() == b"png-9"
