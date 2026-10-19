"""drain_background снимает реестры и пишет таймаут, если задача не умерла."""

from __future__ import annotations

import asyncio

import pytest

from app.services.action_layer import executor


@pytest.mark.asyncio
async def test_drain_background_clears_registries_and_logs_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[tuple] = []
    monkeypatch.setattr(
        executor.logger,
        "warning",
        lambda *args, **kwargs: warnings.append(args),
    )

    async def hang() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(hang(), name="action-layer-drain-probe")
    executor._TASKS["probe"] = task
    executor._ORPHANS[id(task)] = {"task": task}

    async def still_pending(pending, timeout=None):
        return set(), set(pending)

    monkeypatch.setattr(executor.asyncio, "wait", still_pending)
    try:
        await executor.drain_background()
        assert executor._TASKS == {}
        assert executor._ORPHANS == {}
        assert any("timeout" in " ".join(str(part) for part in row) for row in warnings)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        executor._TASKS.pop("probe", None)
        executor._ORPHANS.pop(id(task), None)
