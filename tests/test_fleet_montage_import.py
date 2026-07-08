"""Fleet montage: defer на agent, импорт bundle на hub."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.fleet.bundle import _should_skip_bundle_file, mark_montage_ready
from app.models import Project, ProjectStatus
from app.services.project_steps import start_step
from app.settings import settings


def test_mark_montage_ready_sets_timestamp() -> None:
    meta = mark_montage_ready({})
    assert meta["montage_ready"] is True
    assert isinstance(meta.get("montage_ready_at"), str)
    assert len(meta["montage_ready_at"]) >= 10


def test_should_skip_excel_lock_file() -> None:
    from pathlib import Path

    root = Path("data/videos/proj")
    assert _should_skip_bundle_file(root / "old/~$20260617_project.xlsx", data_root=root) is True
    assert _should_skip_bundle_file(root / "project.xlsx", data_root=root) is False
    assert _should_skip_bundle_file(root / "old/backup.xlsx", data_root=root) is True


@pytest.mark.asyncio
async def test_start_step_assemble_defers_on_agent(monkeypatch) -> None:
    monkeypatch.setattr(settings, "fleet_enabled", True)
    monkeypatch.setattr(settings, "fleet_role", "agent")
    project = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.music_ready,
        meta={"node_step_params": {"assemble": {"send_to_main_pc": True}}},
    )
    session = AsyncMock()
    session.flush = AsyncMock()

    with patch(
        "app.services.project_steps.clear_step_outputs_for_rerun",
        new=AsyncMock(return_value={}),
    ):
        status = await start_step(session, project, "assemble")

    assert status is ProjectStatus.music_ready
    assert project.meta.get("montage_ready") is True
    assert project.meta.get("fleet_montage_deferred") is True


@pytest.mark.asyncio
async def test_start_step_assemble_local_when_send_to_main_pc_off(monkeypatch) -> None:
    monkeypatch.setattr(settings, "fleet_enabled", True)
    monkeypatch.setattr(settings, "fleet_role", "agent")
    project = Project(
        slug="t-local",
        topic="t",
        status=ProjectStatus.music_ready,
        meta={
            "node_step_params": {"assemble": {"send_to_main_pc": False}},
            "fleet_montage_deferred": True,
            "montage_ready": True,
            "montage_ready_at": "2026-06-22T14:00:00",
        },
    )
    session = AsyncMock()
    session.flush = AsyncMock()

    with patch(
        "app.services.project_steps.clear_step_outputs_for_rerun",
        new=AsyncMock(return_value={}),
    ), patch(
        "app.fleet.montage_handoff.defer_assemble_to_hub",
        new=AsyncMock(return_value=True),
    ) as defer_mock:
        status = await start_step(session, project, "assemble")

    defer_mock.assert_not_called()
    assert status is ProjectStatus.assembling
    assert project.meta.get("fleet_montage_deferred") is None
    assert project.meta.get("montage_ready") is None
