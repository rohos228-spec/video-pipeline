"""Тесты сервиса `app.services.scan_frames` — фича «🔍 Добить недостающие».

Главный сценарий, который ломался до фикса:
  * у проекта 12 кадров без `.png` на диске и ~120 кадров с `.png`;
  * после клика «Добить недостающие» воркер ДОЛЖЕН перегенерить только
    те 12, остальные оставить как есть.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.services.scan_frames import (
    disk_has_frame_image,
    reset_frames_to_image_prompt_ready,
    scan_missing_frames,
)
from app.settings import settings


@pytest_asyncio.fixture
async def session(tmp_path: Path, monkeypatch):
    # `Project.data_dir` — это property, которая берёт `settings.data_dir`.
    # Подменяем корень `data/` на tmp_path, чтобы тест не лез в реальный
    # каталог данных.
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db_url = f"sqlite+aiosqlite:///{tmp_path / 't.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as s:
        yield s
    await engine.dispose()


async def _mkproject(session, slug: str = "p1") -> Project:
    """Создаёт Project + одноимённую папку `videos/<slug>/scenes/`,
    чтобы `project.data_dir / 'scenes'` существовал."""
    p = Project(
        slug=slug,
        topic="t",
        hero_mode="auto",
        status=ProjectStatus.image_prompts_ready,
    )
    session.add(p)
    await session.flush()
    (p.data_dir / "scenes").mkdir(parents=True, exist_ok=True)
    return p


async def _mkframe(
    session,
    project: Project,
    n: int,
    *,
    image_prompt: str | None = "p",
    status: FrameStatus = FrameStatus.image_prompt_ready,
) -> Frame:
    fr = Frame(
        project_id=project.id,
        number=n,
        voiceover_text=f"vo {n}",
        image_prompt=image_prompt,
        status=status,
    )
    session.add(fr)
    await session.flush()
    return fr


# ---------------------------------------------------------------------------
# disk_has_frame_image


def test_disk_has_frame_image_finds_png(tmp_path: Path):
    """`frame_NNN_<uuid8>.png` в scenes/ должен распознаваться."""
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    (scenes / "frame_012_36770540.png").write_bytes(b"\x89PNG")
    assert disk_has_frame_image(scenes, 12) is True
    # другие кадры — нет файла → False
    assert disk_has_frame_image(scenes, 13) is False


def test_disk_has_frame_image_no_dir(tmp_path: Path):
    """Если папки scenes/ ещё нет — False, не падаем."""
    scenes = tmp_path / "scenes"
    assert disk_has_frame_image(scenes, 1) is False


def test_disk_has_frame_image_zero_padding(tmp_path: Path):
    """Нумерация всегда 3-значная с ведущими нулями (`frame_001_*.png`)."""
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    (scenes / "frame_001_abcd1234.png").write_bytes(b"x")
    assert disk_has_frame_image(scenes, 1) is True
    # «frame_1_*.png» (без padding) — нет, наш формат строгий
    (scenes / "frame_1_zzz.png").write_bytes(b"x")
    # 1 уже True, проверим что 2 всё ещё False (нет «frame_002_*»)
    assert disk_has_frame_image(scenes, 2) is False


# ---------------------------------------------------------------------------
# scan_missing_frames


@pytest.mark.asyncio
async def test_scan_missing_frames_picks_only_no_png(session):
    """Кадры с .png на диске НЕ должны попадать в missing.

    Сценарий из бага: 12 кадров без .png и 120 с .png → missing == 12 ровно.
    Тут уменьшенная версия — 5 кадров, у 3х есть .png.
    """
    p = await _mkproject(session)
    scenes = p.data_dir / "scenes"

    for i in (1, 2, 3, 4, 5):
        await _mkframe(session, p, i, image_prompt=f"prompt {i}")
    (scenes / "frame_001_abcd1234.png").write_bytes(b"x")
    (scenes / "frame_003_ef567890.png").write_bytes(b"x")
    (scenes / "frame_005_11112222.png").write_bytes(b"x")

    missing = await scan_missing_frames(session, p)
    assert missing == [2, 4]


@pytest.mark.asyncio
async def test_scan_missing_skips_frames_without_prompt(session):
    """Кадры без `image_prompt` НЕ в missing (генерить нечего)."""
    p = await _mkproject(session)
    await _mkframe(session, p, 1, image_prompt=None)
    await _mkframe(session, p, 2, image_prompt="")
    await _mkframe(session, p, 3, image_prompt="real prompt")
    missing = await scan_missing_frames(session, p)
    # только 3, т.к. у 1/2 нет промта вообще
    assert missing == [3]


@pytest.mark.asyncio
async def test_scan_missing_status_irrelevant(session):
    """Диск — источник истины. Если в БД `image_generated`, но файл
    удалили вручную — кадр всё равно попадёт в missing."""
    p = await _mkproject(session)
    # frame 1: image_generated в БД, но файла нет — должен быть missing
    await _mkframe(
        session, p, 1,
        image_prompt="p1", status=FrameStatus.image_generated,
    )
    # frame 2: image_prompt_ready, но файл есть — НЕ missing
    await _mkframe(
        session, p, 2,
        image_prompt="p2", status=FrameStatus.image_prompt_ready,
    )
    (p.data_dir / "scenes" / "frame_002_aabbccdd.png").write_bytes(b"x")
    missing = await scan_missing_frames(session, p)
    assert missing == [1]


# ---------------------------------------------------------------------------
# reset_frames_to_image_prompt_ready


@pytest.mark.asyncio
async def test_reset_only_listed_numbers(session):
    """Меняем статус ТОЛЬКО у указанных кадров — остальные не трогаем."""
    p = await _mkproject(session)
    f1 = await _mkframe(
        session, p, 1,
        image_prompt="p1", status=FrameStatus.image_generated,
    )
    f2 = await _mkframe(
        session, p, 2,
        image_prompt="p2", status=FrameStatus.image_approved,
    )
    f3 = await _mkframe(
        session, p, 3,
        image_prompt="p3", status=FrameStatus.image_generated,
    )

    changed = await reset_frames_to_image_prompt_ready(session, p, [2])
    assert changed == 1
    await session.refresh(f1)
    await session.refresh(f2)
    await session.refresh(f3)
    assert f1.status is FrameStatus.image_generated
    assert f2.status is FrameStatus.image_prompt_ready
    assert f3.status is FrameStatus.image_generated


@pytest.mark.asyncio
async def test_reset_skips_frames_without_prompt(session):
    """Кадры без image_prompt не сбрасываем — генерить нечего."""
    p = await _mkproject(session)
    # frame без image_prompt стартует в planned (ещё нет промта).
    f1 = await _mkframe(
        session, p, 1, image_prompt=None, status=FrameStatus.planned,
    )
    f2 = await _mkframe(session, p, 2, image_prompt="real")
    changed = await reset_frames_to_image_prompt_ready(session, p, [1, 2])
    # только f2 — f1 пропущен из-за отсутствия image_prompt
    assert changed == 1
    await session.refresh(f1)
    await session.refresh(f2)
    assert f1.status is FrameStatus.planned
    assert f2.status is FrameStatus.image_prompt_ready


@pytest.mark.asyncio
async def test_reset_clears_fail_reason(session):
    """`attrs['fail_reason']` чистится при reset — иначе UI бы продолжал
    показывать старую причину фейла после успешной регенерации."""
    p = await _mkproject(session)
    fr = Frame(
        project_id=p.id,
        number=1,
        voiceover_text="vo 1",
        image_prompt="p",
        status=FrameStatus.failed,
        attrs={"fail_reason": "outsee_timeout"},
    )
    session.add(fr)
    await session.flush()
    await reset_frames_to_image_prompt_ready(session, p, [1])
    await session.refresh(fr)
    assert fr.status is FrameStatus.image_prompt_ready
    assert "fail_reason" not in (fr.attrs or {})


@pytest.mark.asyncio
async def test_reset_empty_list_is_noop(session):
    """Пустой список — 0 изменений, не падаем."""
    p = await _mkproject(session)
    await _mkframe(session, p, 1, image_prompt="p")
    assert await reset_frames_to_image_prompt_ready(session, p, []) == 0
