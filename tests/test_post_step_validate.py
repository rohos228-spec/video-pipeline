"""Smoke tests for post_step_validate."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Frame, FrameStatus


def test_import_post_step_validate():
    from app.services import post_step_validate as psv

    assert hasattr(psv, "validate_after_music")
    assert hasattr(psv, "finalize_or_retry")


def _valid_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 250_000)


@pytest.mark.asyncio
async def test_validate_after_images_skips_frames_without_prompt(
    tmp_path: Path, monkeypatch
) -> None:
    """Кадры без image_prompt не блокируют images_ready."""
    from app.services import post_step_validate as psv

    data_dir = tmp_path / "p59"
    scenes = data_dir / "scenes"
    _valid_png(scenes / "frame_001_abc12345.png")

    project = SimpleNamespace(id=59, data_dir=data_dir)
    fr_ok = Frame(
        project_id=59, number=1, image_prompt="wide shot", status=FrameStatus.image_generated
    )
    fr_empty = Frame(
        project_id=59, number=2, image_prompt="", status=FrameStatus.planned
    )
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=lambda: [fr_ok, fr_empty]))
        )
    )

    result = await psv.validate_after_images(session, project)
    assert result.ok is True
    assert result.missing_frame_numbers == []
    assert result.expected_frames == 1


@pytest.mark.asyncio
async def test_validate_after_videos_uses_db_anim_prompts_not_xlsx_count(
    tmp_path: Path, monkeypatch
) -> None:
    """DB 180 vs xlsx 65 не валит videos_ready, если клипы есть у кадров с anim_pr."""
    from app.services import post_step_validate as psv

    data_dir = tmp_path / "p59"
    vids = data_dir / "videos"
    vids.mkdir(parents=True)
    (vids / "clip_001_aaaaaaaa.mp4").write_bytes(b"x" * 5000)
    (vids / "clip_003_bbbbbbbb.mp4").write_bytes(b"x" * 5000)

    project = SimpleNamespace(id=59, data_dir=data_dir)
    frames = [
        Frame(
            project_id=59,
            number=1,
            animation_prompt="pan left",
            status=FrameStatus.video_generated,
        ),
        Frame(
            project_id=59,
            number=2,
            animation_prompt="",
            status=FrameStatus.planned,
        ),
        Frame(
            project_id=59,
            number=3,
            animation_prompt="dolly in",
            status=FrameStatus.video_generated,
        ),
    ]
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=lambda: frames))
        )
    )
    monkeypatch.setattr(
        psv, "recover_scene_videos_from_disk", AsyncMock(return_value=0)
    )
    monkeypatch.setattr(
        psv, "_frames_with_artifact", AsyncMock(return_value=set())
    )

    result = await psv.validate_after_videos(session, project)
    assert result.ok is True
    assert result.missing_frame_numbers == []
    assert result.expected_frames == 2
