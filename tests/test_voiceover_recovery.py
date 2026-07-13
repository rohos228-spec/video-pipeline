"""Глубокое восстановление voiceover — только родительские проекты."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
from app.services.mass_factory import mass_parent_id
from app.services.voiceover_recovery import (
    discover_original_candidates,
    find_original_voiceover,
    is_parent_project,
    restore_all_parent_voiceovers,
    restore_original_voiceover,
    trash_voiceover_file,
)

_ORIGINAL = ("исходный закадровый текст " * 5).strip()
_GPT = ("GPT перезаписал закадровый текст " * 5).strip()


@pytest.fixture
async def session(tmp_path, monkeypatch) -> AsyncSession:
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


def _parent(*, slug: str = "parent-vo") -> Project:
    p = Project(id=1, slug=slug, topic="Parent", status=ProjectStatus.plan_ready)
    p.data_dir.mkdir(parents=True, exist_ok=True)
    (p.data_dir / "project.xlsx").write_bytes(b"x" * 2048)
    return p


def _child(parent_id: int, *, slug: str = "child-vo") -> Project:
    p = Project(id=2, slug=slug, topic="Child", status=ProjectStatus.script_ready)
    p.meta = {"mass_parent_id": parent_id, "mass_lane_position": 1}
    p.data_dir.mkdir(parents=True, exist_ok=True)
    return p


def test_is_parent_project() -> None:
    parent = Project(id=1, slug="p", topic="t", status=ProjectStatus.new)
    child = Project(id=2, slug="c", topic="t", status=ProjectStatus.new)
    child.meta = {"mass_parent_id": 1}
    assert is_parent_project(parent)
    assert not is_parent_project(child)


def test_trash_voiceover_file_keeps_copy(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _parent()
    vo = p.data_dir / "voiceover.txt"
    vo.write_text(_ORIGINAL, encoding="utf-8")
    dest = trash_voiceover_file(p, vo)
    assert dest is not None
    assert not vo.exists()
    assert (p.data_dir / ".trash").is_dir()
    assert list((p.data_dir / ".trash").glob("*voiceover*"))
    assert list((p.data_dir / "old").glob("*_voiceover_deleted.txt"))


def test_ensure_script_input_prefers_oldest_backup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _parent()
    old = p.data_dir / "old"
    old.mkdir(parents=True, exist_ok=True)
    (old / "20260101_100000_voiceover.txt").write_text(_ORIGINAL, encoding="utf-8")
    (old / "20260201_100000_voiceover.txt").write_text(_GPT, encoding="utf-8")
    (p.data_dir / "voiceover.txt").write_text(_GPT, encoding="utf-8")

    path = cx.ensure_script_input_voiceover(p)
    assert path is not None
    assert path.name == "20260101_100000_voiceover.txt"


@pytest.mark.asyncio
async def test_find_from_tmp_gpt_when_no_backup(session: AsyncSession, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _parent()
    session.add(p)
    await session.commit()

    tmp = p.data_dir / "tmp_gpt"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "voiceover_20260101_120000.txt").write_text(_ORIGINAL, encoding="utf-8")
    # voiceover.txt нет — только tmp_gpt

    cand = await find_original_voiceover(session, p)
    assert cand is not None
    assert cand.text == _ORIGINAL
    assert "tmp_gpt" in cand.source


@pytest.mark.asyncio
async def test_find_from_frames_db(session: AsyncSession, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _parent()
    session.add(p)
    await session.flush()
    for i, part in enumerate(["кадр один " * 8, "кадр два " * 8], start=1):
        session.add(
            Frame(
                project_id=p.id,
                number=i,
                voiceover_text=part,
                meaning="",
                image_prompt="",
                animation_prompt="",
                duration_seconds=2.0,
                start_ts=0,
                end_ts=2,
            )
        )
    await session.commit()

    cand = await find_original_voiceover(session, p)
    assert cand is not None
    assert "frames_db" in cand.source


@pytest.mark.asyncio
async def test_restore_skips_child(session: AsyncSession, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    child = _child(parent_id=99)
    (child.data_dir / "voiceover.txt").write_text(_ORIGINAL, encoding="utf-8")
    session.add(child)
    await session.commit()

    result = await restore_original_voiceover(session, child)
    assert result["reason"] == "child_project_skipped"


@pytest.mark.asyncio
async def test_restore_parent_from_trash(session: AsyncSession, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _parent()
    session.add(p)
    await session.commit()

    trash = p.data_dir / ".trash"
    trash.mkdir(parents=True, exist_ok=True)
    (trash / "20260101_100000_voiceover.txt").write_text(_ORIGINAL, encoding="utf-8")
    (p.data_dir / "voiceover.txt").write_text(_GPT, encoding="utf-8")

    result = await restore_original_voiceover(session, p)
    assert result["restored"] is True
    assert (p.data_dir / "voiceover.txt").read_text(encoding="utf-8") == _ORIGINAL


@pytest.mark.asyncio
async def test_restore_all_parents_only(session: AsyncSession, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    parent = _parent(slug="p-all")
    parent.id = 10
    child = _child(parent_id=10, slug="c-all")
    child.id = 11
    session.add_all([parent, child])
    await session.commit()

    for proj, text in ((parent, _ORIGINAL), (child, _ORIGINAL)):
        old = proj.data_dir / "old"
        old.mkdir(parents=True, exist_ok=True)
        (old / "20260101_100000_voiceover.txt").write_text(text, encoding="utf-8")
        (proj.data_dir / "voiceover.txt").write_text(_GPT, encoding="utf-8")

    with patch("app.services.mass_factory.list_mass_children", return_value=[]):
        summary = await restore_all_parent_voiceovers(session, dry_run=False)

    assert summary["parents_total"] == 1
    assert summary["restored"] == 1
    assert (parent.data_dir / "voiceover.txt").read_text(encoding="utf-8") == _ORIGINAL
    assert (child.data_dir / "voiceover.txt").read_text(encoding="utf-8") == _GPT


@pytest.mark.asyncio
async def test_scan_lists_multiple_sources(session: AsyncSession, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _parent()
    session.add(p)
    await session.commit()
    old = p.data_dir / "old"
    old.mkdir(parents=True, exist_ok=True)
    (old / "20260101_100000_voiceover.txt").write_text(_ORIGINAL, encoding="utf-8")
    tmp = p.data_dir / "tmp_gpt"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "voiceover_20260201_120000.txt").write_text(_GPT, encoding="utf-8")

    candidates = await discover_original_candidates(session, p)
    sources = {c.source for c in candidates}
    assert any("old/" in s for s in sources)
    assert any("tmp_gpt" in s for s in sources)
