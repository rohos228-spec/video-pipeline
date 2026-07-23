"""Worker не дублирует generate_audio, пока жив montage_job."""

from __future__ import annotations

import asyncio

import pytest

from app.services.montage_board_montage_job import is_montage_job_live, spawn_montage_job
from app.services.step_cancel import (
    clear_all,
    is_advance_active,
    is_generation_active,
    unregister_advance_task,
)


@pytest.fixture(autouse=True)
def _isolate() -> None:
    clear_all()
    yield
    clear_all()


@pytest.mark.asyncio
async def test_montage_job_blocks_is_generation_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _noop_montage(_pid: int) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(
        "app.services.montage_board_montage_job.run_montage_job",
        _noop_montage,
    )
    task = spawn_montage_job(26)
    try:
        assert is_montage_job_live(26)
        assert is_generation_active(26)
        assert is_advance_active(26)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        unregister_advance_task(26)
