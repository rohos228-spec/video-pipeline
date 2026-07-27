"""Параллельная очередь Create: семафор, waiting/running, отдельные job_id."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _reset_create_jobs(monkeypatch, *, max_parallel: int = 2) -> None:
    from app.services import create_jobs as cj

    monkeypatch.setattr(cj.settings, "create_max_parallel", max_parallel)
    cj._JOBS.clear()
    cj._SEM = None
    cj._SEM_SIZE = 0


@pytest.mark.asyncio
async def test_enqueue_respects_max_parallel_and_unique_ids(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import create_jobs as cj
    from app.services import generation_storage as gs

    monkeypatch.setattr(gs.settings, "data_dir", tmp_path)
    _reset_create_jobs(monkeypatch, max_parallel=2)

    gate = asyncio.Event()
    started: list[str] = []

    async def slow_run(out_path: Path):
        started.append(out_path.name)

        class R:
            file_path = out_path
            raw_url = None

        out_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        await gate.wait()
        return R()

    jobs = []
    for i in range(3):
        job = await cj.enqueue_generation(
            media="image",
            model="gpt-image-2",
            provider="outsee",
            prompt=f"prompt-{i}",
            ext=".png",
            params=None,
            quote=None,
            run=slow_run,
        )
        jobs.append(job)

    assert len({j.id for j in jobs}) == 3

    # даём задачам захватить слоты
    for _ in range(50):
        snap = cj.queue_snapshot()
        if snap["running_count"] == 2 and snap["waiting_count"] == 1:
            break
        await asyncio.sleep(0.02)
    else:
        snap = cj.queue_snapshot()
        pytest.fail(
            f"expected 2 running + 1 waiting, got "
            f"run={snap['running_count']} wait={snap['waiting_count']} "
            f"statuses={[j.status for j in jobs]}"
        )

    assert snap["max_parallel"] == 2
    waiting = snap["waiting"]
    assert len(waiting) == 1
    assert waiting[0]["queue_position"] == 1
    assert waiting[0]["status"] == "queued"
    assert all(j["status"] == "processing" for j in snap["running"])

    gate.set()
    for _ in range(100):
        if all(j.status in {"done", "failed"} for j in jobs):
            break
        await asyncio.sleep(0.02)
    assert all(j.status == "done" for j in jobs)
    assert len(started) == 3
    final = cj.queue_snapshot()
    assert final["total_active"] == 0


@pytest.mark.asyncio
async def test_queue_snapshot_empty_when_idle(monkeypatch, tmp_path: Path) -> None:
    from app.services import create_jobs as cj
    from app.services import generation_storage as gs

    monkeypatch.setattr(gs.settings, "data_dir", tmp_path)
    _reset_create_jobs(monkeypatch, max_parallel=2)
    snap = cj.queue_snapshot()
    assert snap["running"] == []
    assert snap["waiting"] == []
    assert snap["running_count"] == 0
    assert snap["waiting_count"] == 0
    assert snap["max_parallel"] == 2
