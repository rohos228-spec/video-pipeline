"""Ревью #352 п.8: скрытый кадр не идёт в анимацию и видео, доска отдаёт признак."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project
from app.orchestrator.steps.generate_images import frame_hidden_from_paid_generation
from app.orchestrator.steps.generate_videos import _skip_frame_video_generation
from app.services.animation_prompt_gpt import collect_batch_items
from app.services.montage_board import build_montage_board


def test_animation_and_video_skip_hidden_frame(tmp_path: Path) -> None:
    project = SimpleNamespace(id=7, data_dir=tmp_path)
    hidden = SimpleNamespace(
        number=2,
        image_prompt="герой стоит у двери на общем плане",
        animation_prompt="",
        voiceover_text="скрытый кадр",
        status=FrameStatus.planned,
        attrs={"активен": False, "удалён": True},
    )
    live = SimpleNamespace(
        number=3,
        image_prompt="герой стоит у двери на общем плане",
        animation_prompt="",
        voiceover_text="живой кадр",
        status=FrameStatus.planned,
        attrs={},
    )
    items = collect_batch_items(project, [hidden, live])
    assert [item.frame.number for item in items] == [3]
    assert frame_hidden_from_paid_generation(hidden) is True
    assert _skip_frame_video_generation(hidden, False) is True
    assert _skip_frame_video_generation(live, False) is False


@pytest.mark.asyncio
async def test_board_returns_hidden_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    project = Project(id=8, slug="paid-hide", topic="t", hero_mode="auto")
    project.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(project)
        session.add(
            Frame(
                project_id=project.id,
                number=1,
                voiceover_text="скрыт",
                status="planned",
                attrs={"активен": False, "удалён": True},
            )
        )
        await session.flush()
        board = await build_montage_board(session, project)
    await engine.dispose()
    assert board["frames"][0]["скрыт"] is True
