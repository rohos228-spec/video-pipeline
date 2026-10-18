"""№16: dev-сид монтажа не стирает кадры и не стартует на проекте по умолчанию."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Entity, Frame, FrameText, Project, PromptVersion
from app.services import db_v2
from scripts.dev_seed_montage_scene import (
    archive_project_frames,
    missing_asset_rows,
    require_project_id,
)

_SCRIPT = Path("scripts/dev_seed_montage_scene.py")


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def test_require_project_id_needs_env_and_explicit_id(monkeypatch) -> None:
    monkeypatch.delenv("VP_DEV_SEED", raising=False)
    with pytest.raises(SystemExit):
        require_project_id(["5"])
    monkeypatch.setenv("VP_DEV_SEED", "1")
    with pytest.raises(SystemExit):
        require_project_id([])
    with pytest.raises(SystemExit):
        require_project_id(["0"])
    with pytest.raises(SystemExit):
        require_project_id(["-3"])
    with pytest.raises(SystemExit):
        require_project_id(["abc"])
    assert require_project_id(["12"]) == 12


def test_seed_script_has_no_frame_or_entity_delete() -> None:
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "delete(Frame)" not in src
    assert "delete(Entity)" not in src
    assert "else 1" not in src


def test_missing_asset_rows_skip_existing_codes() -> None:
    rows = missing_asset_rows({"c01"})
    codes = [row[1] for row in rows]
    assert "c01" not in codes
    assert "c02" in codes
    assert "i01" in codes


@pytest.mark.asyncio
async def test_archive_project_frames_keeps_rows(session: AsyncSession) -> None:
    project = Project(slug="item16", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    old = Frame(
        project_id=project.id,
        number=1,
        uuid="seed-old",
        voiceover_text="живой кадр",
        attrs={"keep": 1},
    )
    session.add(old)
    await session.flush()
    session.add(
        FrameText(
            project_id=project.id,
            frame_id=old.id,
            kind="voiceover",
            text="живой кадр",
        )
    )
    session.add(
        PromptVersion(
            project_id=project.id,
            frame_id=old.id,
            kind="img",
            text="промт",
        )
    )
    session.add(
        Entity(
            project_id=project.id,
            type="character",
            code="c01",
            name="уже есть",
            sort_key=1.0,
        )
    )
    await session.commit()

    n = await archive_project_frames(session, project.id)
    await session.commit()
    assert n == 1
    frames = list(
        (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars()
    )
    assert len(frames) == 1
    assert frames[0].uuid == "seed-old"
    assert frames[0].voiceover_text == "живой кадр"
    assert int(frames[0].number) >= 1_000_000
    assert (frames[0].attrs or {}).get("archived") is True
    assert ((frames[0].attrs or {}).get("camera_subdivide") or {}).get("leftover") is True
    texts = list(
        (await session.execute(select(FrameText).where(FrameText.frame_id == old.id))).scalars()
    )
    prompts = list(
        (
            await session.execute(
                select(PromptVersion).where(PromptVersion.frame_id == old.id)
            )
        ).scalars()
    )
    ents = list(
        (await session.execute(select(Entity).where(Entity.project_id == project.id))).scalars()
    )
    assert texts[0].text == "живой кадр"
    assert prompts[0].text == "промт"
    assert [e.code for e in ents] == ["c01"]
