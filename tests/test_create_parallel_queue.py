"""Параллельная очередь Create: семафор, waiting/running, отдельные job_id."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def _reset_create_jobs(
    monkeypatch,
    *,
    outsee: int = 2,
    grsai: int = 10,
) -> None:
    from app.services import create_jobs as cj

    monkeypatch.setattr(cj.settings, "create_max_parallel", max(outsee, grsai))
    monkeypatch.setattr(cj.settings, "create_max_parallel_outsee", outsee)
    monkeypatch.setattr(cj.settings, "create_max_parallel_grsai", grsai)
    cj._JOBS.clear()
    cj._SEMS.clear()
    cj._SEM_SIZES.clear()
    cj._TASKS.clear()


@pytest.mark.asyncio
async def test_enqueue_respects_max_parallel_and_unique_ids(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import create_jobs as cj
    from app.services import generation_storage as gs

    monkeypatch.setattr(gs.settings, "data_dir", tmp_path)
    _reset_create_jobs(monkeypatch, outsee=2)

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

    for _ in range(50):
        snap = cj.queue_snapshot(provider="outsee")
        if snap["running_count"] == 2 and snap["waiting_count"] == 1:
            break
        await asyncio.sleep(0.02)
    else:
        snap = cj.queue_snapshot(provider="outsee")
        pytest.fail(
            f"expected 2 running + 1 waiting, got "
            f"run={snap['running_count']} wait={snap['waiting_count']} "
            f"statuses={[j.status for j in jobs]}"
        )

    assert snap["max_parallel"] == 2
    assert snap["max_parallel_outsee"] == 2
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
    assert len(cj._TASKS) == 0


@pytest.mark.asyncio
async def test_outsee_and_grsai_use_separate_pools(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import create_jobs as cj
    from app.services import generation_storage as gs

    monkeypatch.setattr(gs.settings, "data_dir", tmp_path)
    _reset_create_jobs(monkeypatch, outsee=1, grsai=2)

    gate = asyncio.Event()

    async def slow_run(out_path: Path):
        class R:
            file_path = out_path
            raw_url = None

        out_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        await gate.wait()
        return R()

    # 2 outsee при лимите 1 → 1 running + 1 waiting
    o1 = await cj.enqueue_generation(
        media="image",
        model="gpt-image-2",
        provider="outsee",
        prompt="o1",
        ext=".png",
        params=None,
        quote=None,
        run=slow_run,
    )
    o2 = await cj.enqueue_generation(
        media="image",
        model="gpt-image-2",
        provider="outsee",
        prompt="o2",
        ext=".png",
        params=None,
        quote=None,
        run=slow_run,
    )
    # grsai не должен ждать outsee-слот
    g1 = await cj.enqueue_generation(
        media="image",
        model="gpt-image-2",
        provider="grsai",
        prompt="g1",
        ext=".png",
        params=None,
        quote=None,
        run=slow_run,
    )

    for _ in range(50):
        so = cj.queue_snapshot(provider="outsee")
        sg = cj.queue_snapshot(provider="grsai")
        if (
            so["running_count"] == 1
            and so["waiting_count"] == 1
            and sg["running_count"] == 1
        ):
            break
        await asyncio.sleep(0.02)
    else:
        so = cj.queue_snapshot(provider="outsee")
        sg = cj.queue_snapshot(provider="grsai")
        pytest.fail(f"outsee={so} grsai={sg} statuses o={[o1.status,o2.status]} g={g1.status}")

    assert cj.max_parallel("outsee") == 1
    assert cj.max_parallel("grsai") == 2
    gate.set()
    for _ in range(100):
        if all(j.status in {"done", "failed"} for j in (o1, o2, g1)):
            break
        await asyncio.sleep(0.02)
    assert all(j.status == "done" for j in (o1, o2, g1))


@pytest.mark.asyncio
async def test_queue_snapshot_empty_when_idle(monkeypatch, tmp_path: Path) -> None:
    from app.services import create_jobs as cj
    from app.services import generation_storage as gs

    monkeypatch.setattr(gs.settings, "data_dir", tmp_path)
    _reset_create_jobs(monkeypatch, outsee=5, grsai=10)
    snap = cj.queue_snapshot()
    assert snap["running"] == []
    assert snap["waiting"] == []
    assert snap["running_count"] == 0
    assert snap["waiting_count"] == 0
    assert snap["max_parallel_outsee"] == 5
    assert snap["max_parallel_grsai"] == 10
    assert snap["max_parallel"] == 10
