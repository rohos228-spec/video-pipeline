"""Восстановление исходного voiceover — только родительские проекты."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
from app.services.mass_factory import mass_parent_id
from app.services.voiceover_recovery import (
    find_original_voiceover,
    is_parent_project,
    restore_all_parent_voiceovers,
    restore_original_voiceover,
)


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


def _parent(tmp_path, *, slug: str = "parent-vo") -> Project:
    p = Project(id=1, slug=slug, topic="Parent", status=ProjectStatus.plan_ready)
    p.data_dir.mkdir(parents=True, exist_ok=True)
    (p.data_dir / "project.xlsx").write_bytes(b"x" * 2048)
    return p


def _child(tmp_path, parent_id: int, *, slug: str = "child-vo") -> Project:
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


def test_ensure_script_input_voiceover_prefers_oldest_backup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _parent(tmp_path)
    old = p.data_dir / "old"
    old.mkdir(parents=True, exist_ok=True)
    (old / "20260101_100000_voiceover.txt").write_text("исходный", encoding="utf-8")
    (old / "20260201_100000_voiceover.txt").write_text("позже", encoding="utf-8")
    (p.data_dir / "voiceover.txt").write_text("текущий GPT", encoding="utf-8")

    path = cx.ensure_script_input_voiceover(p)
    assert path is not None
    assert path.name == "20260101_100000_voiceover.txt"
    assert path.read_text(encoding="utf-8") == "исходный"


@pytest.mark.asyncio
async def test_find_original_from_oldest_backup(session: AsyncSession, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _parent(tmp_path)
    session.add(p)
    await session.commit()

    old = p.data_dir / "old"
    old.mkdir(parents=True, exist_ok=True)
    (old / "20260101_100000_voiceover.txt").write_text("оригинал", encoding="utf-8")
    (old / "20260201_100000_voiceover.txt").write_text("вторая версия", encoding="utf-8")
    (p.data_dir / "voiceover.txt").write_text("GPT сейчас", encoding="utf-8")

    cand = await find_original_voiceover(session, p)
    assert cand is not None
    assert cand.text == "оригинал"
    assert cand.source == "old/20260101_100000_voiceover.txt"


@pytest.mark.asyncio
async def test_restore_skips_child_project(session: AsyncSession, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    child = _child(tmp_path, parent_id=99)
    (child.data_dir / "voiceover.txt").write_text("child text", encoding="utf-8")
    session.add(child)
    await session.commit()

    result = await restore_original_voiceover(session, child)
    assert result["restored"] is False
    assert result["reason"] == "child_project_skipped"
    assert mass_parent_id(child) == 99


@pytest.mark.asyncio
async def test_restore_parent_from_backup(session: AsyncSession, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _parent(tmp_path)
    session.add(p)
    await session.commit()

    old = p.data_dir / "old"
    old.mkdir(parents=True, exist_ok=True)
    (old / "20260101_100000_voiceover.txt").write_text("восстановить меня", encoding="utf-8")
    (p.data_dir / "voiceover.txt").write_text("GPT перезапись", encoding="utf-8")
    p.script_text = "GPT перезапись"

    result = await restore_original_voiceover(session, p)
    assert result["restored"] is True
    assert result["source"] == "old/20260101_100000_voiceover.txt"
    assert (p.data_dir / "voiceover.txt").read_text(encoding="utf-8") == "восстановить меня"
    assert p.script_text == "восстановить меня"


@pytest.mark.asyncio
async def test_restore_all_parents_only(session: AsyncSession, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    parent = _parent(tmp_path, slug="p-all")
    parent.id = 10
    child = _child(tmp_path, parent_id=10, slug="c-all")
    child.id = 11
    session.add_all([parent, child])
    await session.commit()

    for proj, text in ((parent, "parent orig"), (child, "child text")):
        old = proj.data_dir / "old"
        old.mkdir(parents=True, exist_ok=True)
        (old / "20260101_100000_voiceover.txt").write_text(text, encoding="utf-8")
        (proj.data_dir / "voiceover.txt").write_text("bad", encoding="utf-8")

    with patch(
        "app.services.mass_factory.list_mass_children",
        return_value=[],
    ):
        summary = await restore_all_parent_voiceovers(session, dry_run=False)

    assert summary["parents_total"] == 1
    assert summary["restored"] == 1
    assert (parent.data_dir / "voiceover.txt").read_text(encoding="utf-8") == "parent orig"
    assert (child.data_dir / "voiceover.txt").read_text(encoding="utf-8") == "bad"
