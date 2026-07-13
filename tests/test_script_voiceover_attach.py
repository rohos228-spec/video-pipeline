"""Исходный voiceover.txt обязателен при запуске шага script (если есть в проекте)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
from app.services import xlsx_step_runners as xsr
from app.services.gpt_verdict_review import attachments_for_step


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


def _project(tmp_path, *, script_text: str = "", voiceover: str | None = None) -> Project:
    from app import settings as app_settings

    p = Project(id=7, slug="vo-test", topic="t", status=ProjectStatus.plan_ready)
    p.data_dir.mkdir(parents=True, exist_ok=True)
    (p.data_dir / "project.xlsx").write_bytes(b"x" * 2048)
    if voiceover is not None:
        (p.data_dir / "voiceover.txt").write_text(voiceover, encoding="utf-8")
    if script_text:
        p.script_text = script_text
    return p


def test_ensure_source_voiceover_from_file(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _project(tmp_path, voiceover="уже на диске")
    path = cx.ensure_source_voiceover(p)
    assert path is not None
    assert path.read_text(encoding="utf-8") == "уже на диске"


def test_ensure_source_voiceover_syncs_script_text(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _project(tmp_path, script_text="текст из БД")
    path = cx.ensure_source_voiceover(p)
    assert path is not None
    assert path.read_text(encoding="utf-8") == "текст из БД"


def test_ensure_source_voiceover_missing_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _project(tmp_path)
    assert cx.ensure_source_voiceover(p) is None


def test_ensure_source_voiceover_from_backup(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _project(tmp_path)
    old = p.data_dir / "old"
    old.mkdir(parents=True, exist_ok=True)
    (old / "20260101_120000_voiceover.txt").write_text("из бэкапа", encoding="utf-8")
    path = cx.ensure_source_voiceover(p)
    assert path is not None
    assert path.read_text(encoding="utf-8") == "из бэкапа"


@pytest.mark.asyncio
async def test_run_script_xlsx_attaches_source_voiceover(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _project(tmp_path, script_text="исходный закадровый")
    captured: list[list[Path]] = []

    async def fake_ask(chat_msg: str, files: list[Path], downloaded: Path, **kwargs: object) -> str:
        captured.append(list(files))
        downloaded.write_text("x" * 200, encoding="utf-8")
        return "ok"

    prompt_path = p.data_dir / "tmp_gpt" / "prompt_script.txt"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("prompt", encoding="utf-8")

    async def fake_lock(_pid: int, _step: str, fn):
        return await fn()

    with (
        patch.object(xsr.xgf, "telegram_style_ask_and_download", side_effect=fake_ask),
        patch.object(xsr.xgf, "run_under_xlsx_lock", side_effect=fake_lock),
        patch.object(xsr.cx, "write_script_prompt_file", return_value=prompt_path),
        patch.object(xsr.cx, "chat_message", return_value="go"),
    ):
        await xsr.run_script_xlsx(p)

    assert len(captured) == 1
    names = [f.name for f in captured[0]]
    assert "project.xlsx" in names
    assert "voiceover.txt" in names


@pytest.mark.asyncio
async def test_attachments_for_step_script_includes_voiceover_from_db(
    session: AsyncSession, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = _project(tmp_path, script_text="для вердикта")
    session.add(p)
    await session.commit()

    files = await attachments_for_step(session, p, "script")
    assert any(f.name == "voiceover.txt" for f in files)
