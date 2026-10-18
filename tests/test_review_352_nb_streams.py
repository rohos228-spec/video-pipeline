"""Неблокирующее ревью #352: vision-батчи не поднимают check_streams до числа пачек."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_vision_batches_keep_configured_stream_cap(monkeypatch, tmp_path: Path) -> None:
    from app.services.gpt_operator_client import (
        OperatorApiResult,
        _run_check_vision_batched,
    )

    seen: list[int] = []

    @asynccontextmanager
    async def _slot(streams: int):
        seen.append(streams)
        yield

    async def _real(**kwargs):
        return OperatorApiResult(reply_text="verdict: pass\n", output_paths=[])

    monkeypatch.setattr("app.services.check_streams.acquire_check_slot", _slot)
    monkeypatch.setattr(
        "app.services.gpt_operator_client._run_operator_api_real",
        _real,
    )

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("asyncio.sleep", _no_sleep)
    images = [tmp_path / f"f{i}.png" for i in range(9)]
    await _run_check_vision_batched(
        project_dir=tmp_path,
        node_key="img_pr",
        role="check",
        output_mode="text",
        prompt="проверь",
        accompanying="",
        input_paths=images,
        check_streams=1,
    )
    assert seen
    assert set(seen) == {1}
