"""Ручная раскладка файлов → без генерации."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.models import ArtifactKind, Project, ProjectStatus
from app.services.artifact_recovery import recover_all_media_from_disk
from app.services.project_steps import start_step
from app.telegram.menu import step_by_code


@pytest.mark.asyncio
async def test_recover_audio_from_disk_registers_artifact(tmp_path: Path) -> None:
    from app.models import Frame
    from sqlalchemy import select

    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "voice_full.mp3").write_bytes(b"fake")

    p = Project(id=99, topic="t", slug="manual-audio-test")
    p.general_plan = "plan"
    p.script_text = "script"

    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=type(
            "R",
            (),
            {
                "scalar_one_or_none": lambda self: None,
                "scalars": lambda self: type(
                    "S", (), {"all": lambda self: []}
                )(),
            },
        )()
    )
    session.flush = AsyncMock()
    session.add = lambda obj: None

    with patch.object(Project, "data_dir", new=property(lambda self: tmp_path)):
        stats = await recover_all_media_from_disk(session, p)

    assert stats["audio"] is True


@pytest.mark.asyncio
async def test_start_step_audio_skips_when_voice_on_disk(tmp_path: Path) -> None:
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    (audio_dir / "voice.mp3").write_bytes(b"fake")

    p = Project(id=1, topic="t", slug="skip-audio")
    p.status = ProjectStatus.videos_ready
    p.general_plan = "plan"
    p.script_text = "script"

    session = AsyncMock()
    session.flush = AsyncMock()

    step = step_by_code("audio")
    assert step is not None

    with patch.object(Project, "data_dir", new=property(lambda self: tmp_path)), patch(
        "app.services.project_steps.sync_project_xlsx",
        new=AsyncMock(return_value={}),
    ), patch(
        "app.services.project_steps.clear_step_outputs_for_rerun",
        new=AsyncMock(return_value={}),
    ), patch(
        "app.services.project_steps.purge_tmp_gpt_for_step",
        return_value=None,
    ), patch(
        "app.services.project_state.compute_actual_status",
        new=AsyncMock(return_value=ProjectStatus.audio_ready),
    ):
        st = await start_step(session, p, "audio")

    assert st is ProjectStatus.audio_ready
    assert p.status is ProjectStatus.audio_ready
