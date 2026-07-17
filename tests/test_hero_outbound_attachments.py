"""Hero: отправляемые файлы ≠ выходные PNG; refs без файла не крутят batch."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Artifact,
    ArtifactKind,
    Base,
    HITLDecision,
    HITLKind,
    HITLRequest,
    Project,
    ProjectStatus,
)
from app.orchestrator.steps import generate_hero
from app.services.excel_characters import ExcelCharacter
from app.services.gpt_verdict_review import attachments_for_step
from app.services.reset_step import _wipe_hero


@pytest.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_hero_outbound_attachments_exclude_output_pngs(session, tmp_path) -> None:
    """V-меню «Отправляемые» не должно показывать c01.png как вход."""
    data = tmp_path / "data" / "videos" / "hero-att"
    data.mkdir(parents=True)
    xlsx = data / "project.xlsx"
    xlsx.write_bytes(b"PK" + b"\x00" * 100)
    png = data / "characters" / "c01.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"\x89PNG" + b"\x00" * 50)

    p = Project(slug="hero-att", topic="t", status=ProjectStatus.frames_ready)
    session.add(p)
    await session.flush()
    # data_dir resolves via settings — point project via monkeypatched DATA_DIR
    session.add(
        Artifact(
            project_id=p.id,
            kind=ArtifactKind.hero_reference,
            uuid="u1",
            path=str(png),
            meta={"excel_id": "c01"},
        )
    )
    await session.flush()

    outbound = await attachments_for_step(session, p, "hero")
    names = [f.name for f in outbound]
    assert "project.xlsx" in names
    assert "c01.png" not in names

    verdict = await attachments_for_step(
        session, p, "hero", include_result_artifacts=True
    )
    vnames = [f.name for f in verdict]
    assert "c01.png" in vnames


@pytest.mark.asyncio
async def test_items_outbound_excludes_hero_pngs(session, tmp_path) -> None:
    data = tmp_path / "data" / "videos" / "items-att"
    data.mkdir(parents=True)
    xlsx = data / "project.xlsx"
    xlsx.write_bytes(b"PK" + b"\x00" * 100)
    png = data / "characters" / "c01.png"
    png.parent.mkdir(parents=True)
    png.write_bytes(b"\x89PNG" + b"\x00" * 20)

    p = Project(slug="items-att", topic="t", status=ProjectStatus.hero_ready)
    session.add(p)
    await session.flush()
    session.add(
        Artifact(
            project_id=p.id,
            kind=ArtifactKind.hero_reference,
            uuid="u1",
            path=str(png),
            meta={"excel_id": "c01"},
        )
    )
    await session.flush()

    names = [f.name for f in await attachments_for_step(session, p, "items")]
    assert "c01.png" not in names


@pytest.mark.asyncio
async def test_wipe_hero_resets_hitl_approvals(session, tmp_path) -> None:
    data = tmp_path / "data" / "videos" / "wipe-hitl"
    data.mkdir(parents=True)
    png = data / "c01.png"
    png.write_bytes(b"\x89PNG" + b"\x00" * 20)
    p = Project(slug="wipe-hitl", topic="t", status=ProjectStatus.hero_ready)
    session.add(p)
    await session.flush()
    session.add(
        Artifact(
            project_id=p.id,
            kind=ArtifactKind.hero_reference,
            uuid="u1",
            path=str(png),
            meta={"excel_id": "c01"},
        )
    )
    session.add(
        HITLRequest(
            project_id=p.id,
            kind=HITLKind.approve_hero,
            decision=HITLDecision.approved,
            payload={"excel_id": "c01"},
        )
    )
    await session.flush()

    from sqlalchemy import select

    details = await _wipe_hero(session, p)
    await session.flush()
    assert details.get("hitl_hero_reset", 0) >= 1
    row = (
        await session.execute(select(HITLRequest).where(HITLRequest.project_id == p.id))
    ).scalars().first()
    assert row is not None
    assert row.decision is HITLDecision.pending


@pytest.mark.asyncio
async def test_run_excel_missing_ref_file_exits_without_loop(monkeypatch) -> None:
    """approved без файла не должен крутить while True."""
    calls = {"n": 0}

    async def fake_gen(session, project, bot, ch, **kwargs):
        calls["n"] += 1
        project.status = ProjectStatus.frames_ready

    async def no_regen(*_a, **_k):
        return False

    monkeypatch.setattr(generate_hero, "_generate_one_excel_character", fake_gen)
    monkeypatch.setattr(generate_hero, "_is_regen_for_excel_id", no_regen)

    chars = [
        ExcelCharacter(id="c01", name="a", look="b", ref_ids=[]),
        ExcelCharacter(id="c04", name="c", look="d", ref_ids=["c01"]),
    ]
    p = Project(
        id=42,
        slug="loop",
        topic="t",
        status=ProjectStatus.generating_hero,
        auto_mode=True,
        meta={"excel_hero": {"characters": [c.to_dict() for c in chars]}},
    )

    async def approved(_s, _p):
        return {"c01"}  # stale approve, no file

    async def generated(_s, _p):
        return set()

    monkeypatch.setattr(generate_hero, "_approved_excel_ids", approved)
    monkeypatch.setattr(generate_hero, "_excel_ids_with_artifact", generated)
    monkeypatch.setattr(generate_hero, "_excel_batch_auto", lambda _p: True)

    bot = AsyncMock()
    session = AsyncMock()
    session.refresh = AsyncMock()
    session.flush = AsyncMock()

    cfg = {"characters": [c.to_dict() for c in chars]}
    await generate_hero._run_excel(session, p, bot, cfg)

    assert calls["n"] == 1
    assert p.status is ProjectStatus.frames_ready
