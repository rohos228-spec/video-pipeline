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
