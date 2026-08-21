"""split_frames must export DB→xlsx before node snapshot (UI reads R49)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Frame, Project, ProjectStatus
from app.orchestrator.steps import split_frames


@pytest.mark.asyncio
async def test_split_frames_exports_xlsx_before_snapshot():
    project = MagicMock(spec=Project)
    project.id = 33
    project.status = ProjectStatus.splitting
    project.meta = {}
    project.data_dir = MagicMock()

    existing = []  # force GPT path
    created = [
        MagicMock(spec=Frame, number=i, voiceover_text=f"vo{i}")
        for i in range(1, 4)
    ]

    session = AsyncMock()
    # first execute: existing frames empty; later: frames for export + final check
    exec_results = [
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=existing)))),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=created)))),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=created)))),
    ]
    session.execute = AsyncMock(side_effect=exec_results)
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    result = MagicMock()
    result.apply_ops = [{"target": "replace_frames", "frames": [{"закадр": "a"}, {"закадр": "b"}, {"закадр": "c"}]}]
    result.frames_spec = None

    export_calls: list = []

    with (
        patch.object(split_frames.xsr, "run_split_xlsx", AsyncMock(return_value=result)),
        patch("app.services.db_apply.apply_ops", AsyncMock(return_value={"replace_frames": 3})) as apply_ops,
        patch("app.services.db_apply.export_project_xlsx", side_effect=lambda *a, **k: export_calls.append(1) or {"frames": 3, "cells": 9}) as export_fn,
        patch("app.services.node_xlsx_snapshot.snapshot_and_bind_node_xlsx", AsyncMock()) as snap,
        patch("app.services.storage_step_sync.sync_storage_after_step", AsyncMock()),
        patch("app.services.agent_harness.harness_gate_or_raise", AsyncMock()),
        patch.object(split_frames, "_sheet_for_project", return_value=MagicMock()),
        patch("app.services.node_step_params.split_params_fingerprint", return_value="fp"),
    ):
        await split_frames.run(session, project, bot=None)

    apply_ops.assert_awaited()
    assert apply_ops.await_args.kwargs.get("export_xlsx") is False
    export_fn.assert_called_once()
    snap.assert_awaited_once()
    assert export_calls, "export must run before snapshot"
    assert project.status is ProjectStatus.frames_ready
    assert project.meta.get("split_completed") is True
