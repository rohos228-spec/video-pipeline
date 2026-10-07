"""W3-4 / B08: состояние Create-задач (`_JOBS`) в БД и пережитый перезапуск.

Класс доказательства U и D: настоящий create_jobs, SQLite во временной папке, «генератор» - заменитель.
Перезапуск моделируется сбросом памяти и сменой токена процесса; настоящего рестарта Studio не было.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace

import pytest

from app.services import create_jobs as cj
from app.services.generation_storage import update_sidecar


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("VP_CREATE_JOBS_DB", raising=False)
    cj._JOBS.clear()
    cj._RECENT_FP.clear()
    cj._RESTORED.clear()
    cj._ENSURED.clear()
    cj._SEMS.clear()
    cj._SEM_SIZES.clear()
    yield
    for t in list(cj._TASKS):
        t.cancel()
    cj._JOBS.clear()
    cj._RESTORED.clear()


def _restart(monkeypatch, owner="new-process"):
    """Перезапуск процесса: память пуста, токен процесса другой, БД та же."""
    monkeypatch.setattr(cj, "_OWNER", owner)
    cj._JOBS.clear()
    cj._RECENT_FP.clear()
    cj._RESTORED.clear()


def _db_rows():
    con = sqlite3.connect(str(cj._db_path()))
    con.row_factory = sqlite3.Row
    try:
        return {r["job_id"]: dict(r) for r in con.execute("SELECT * FROM create_jobs")}
    finally:
        con.close()


async def _finish(job):
    task = cj._TASKS_BY_JOB.get(job.id)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 5)  # включая запись состояния в БД
    assert job.status in ("done", "failed"), job.status


def _ok_run(payload: bytes = b"x" * 64):
    async def run(path):
        path.write_bytes(payload)
        return SimpleNamespace(file_path=path, raw_url="https://example.invalid/raw.png")
    return run


async def _enqueue(run, prompt="кошка", model="m1"):
    return await cj.enqueue_generation(media="image", model=model, provider="outsee", prompt=prompt,
                                       ext="png", params=None, quote=None, run=run)


def test_ddl_constant_matches_columns(tmp_path):
    con = sqlite3.connect(tmp_path / "x.db")
    con.executescript(cj.CREATE_JOBS_DDL)
    cols = tuple(r[1] for r in con.execute("PRAGMA table_info(create_jobs)"))
    assert set(cj._JOB_COLUMNS) == set(cols)


async def test_job_lifecycle_is_persisted():
    job = await _enqueue(_ok_run())
    await _finish(job)
    row = _db_rows()[job.id]
    assert row["status"] == "done" and row["error"] is None and row["finished_at"]
    assert row["media"] == "image" and row["provider"] == "outsee" and row["owner"] == cj._OWNER
    assert row["path"] == str(job.path) and row["raw_url"].endswith("raw.png")


async def test_failure_error_is_persisted():
    async def bad(path):
        raise RuntimeError("провайдер вернул 500")
    job = await _enqueue(bad)
    await _finish(job)
    row = _db_rows()[job.id]
    assert row["status"] == "failed" and "провайдер вернул 500" in row["error"]


async def test_done_job_survives_restart(monkeypatch):
    job = await _enqueue(_ok_run())
    await _finish(job)
    old_path, old_url = job.path, job.preview_url
    _restart(monkeypatch)
    assert cj._JOBS == {}
    got = cj.get_job(job.id)  # ленивое восстановление
    assert got is not None and got.status == "done" and got.path == old_path and got.preview_url == old_url
    assert got.to_dict()["ok"] is True and got.to_dict()["bytes"] >= 32


async def test_failed_job_survives_restart_with_error(monkeypatch):
    async def bad(path):
        raise RuntimeError("quota")
    job = await _enqueue(bad)
    await _finish(job)
    _restart(monkeypatch)
    got = cj.get_job(job.id)
    assert got.status == "failed" and "quota" in got.error


async def test_unfinished_job_is_marked_interrupted_not_rerun(monkeypatch):
    gate = asyncio.Event()
    calls = []

    async def slow(path):
        calls.append(1)
        await gate.wait()
        path.write_bytes(b"x" * 64)
        return SimpleNamespace(file_path=path)

    job = await _enqueue(slow)
    for _ in range(200):
        if job.status == "processing" and _db_rows().get(job.id, {}).get("status") == "running":
            break
        await asyncio.sleep(0.01)
    assert _db_rows()[job.id]["status"] == "running"
    side = job.path.with_suffix(".json")
    assert json.loads(side.read_text(encoding="utf-8"))["status"] == "processing"

    _restart(monkeypatch)
    got = cj.get_job(job.id)
    assert got.status == "failed" and got.error == cj.INTERRUPTED_ERROR_RU
    assert got.id not in {j.id for j in cj.list_active_jobs()}
    assert _db_rows()[job.id]["status"] == "interrupted"
    assert json.loads(side.read_text(encoding="utf-8"))["status"] == "failed"
    assert calls == [1]  # повторного платного запуска нет
    # повторное чтение не меняет результат
    _restart(monkeypatch, owner="third-process")
    assert cj.get_job(job.id).status == "failed"
    assert _db_rows()[job.id]["status"] == "interrupted"


async def test_own_running_job_is_not_interrupted_by_restore():
    gate = asyncio.Event()

    async def slow(path):
        await gate.wait()
        path.write_bytes(b"x" * 64)
        return SimpleNamespace(file_path=path)

    job = await _enqueue(slow)
    await asyncio.sleep(0.05)
    cj._RESTORED.clear()
    cj.restore_jobs()
    assert cj._JOBS[job.id] is job and job.status in ("queued", "processing")
    assert _db_rows()[job.id]["status"] in ("queued", "running")
    gate.set()
    await _finish(job)
    assert _db_rows()[job.id]["status"] == "done"


async def test_cancel_is_persisted_as_cancelled_and_loads_as_failed(monkeypatch):
    gate = asyncio.Event()

    async def slow(path):
        await gate.wait()
        return SimpleNamespace(file_path=path)

    job = await _enqueue(slow)
    await asyncio.sleep(0.05)
    assert await cj.cancel_job(job.id) is True
    await asyncio.sleep(0.05)
    assert _db_rows()[job.id]["status"] == "cancelled"
    _restart(monkeypatch)
    assert cj.get_job(job.id).status == "failed"


async def test_old_snapshot_does_not_overwrite_newer_state():
    job = await _enqueue(_ok_run())
    await _finish(job)
    stale = {**cj._snapshot(job, "running"), "seq": 1}
    stale["owner"] = cj._OWNER
    assert cj._persist_sync(stale) is True
    assert _db_rows()[job.id]["status"] == "done"


async def test_db_failure_does_not_break_generation(monkeypatch, tmp_path):
    monkeypatch.setattr(cj, "_db_path", lambda: tmp_path)  # каталог вместо файла БД
    job = await _enqueue(_ok_run())
    await _finish(job)
    assert job.status == "done"
    assert cj.persistence_stats()["skipped"] >= 1


async def test_switch_off_env(monkeypatch):
    monkeypatch.setenv("VP_CREATE_JOBS_DB", "0")
    job = await _enqueue(_ok_run())
    await _finish(job)
    assert job.status == "done" and not cj._db_path().exists()


async def test_prompt_and_error_are_redacted_in_db():
    async def bad(path):
        raise RuntimeError("bad key sk-abcdefghijklmnop1234")
    job = await _enqueue(bad, prompt="рисуй sk-zzzzzzzzzzzzzzzz9999")
    await _finish(job)
    row = _db_rows()[job.id]
    assert "sk-abcdefghijklmnop1234" not in row["error"]
    assert "sk-zzzzzzzzzzzzzzzz9999" not in row["prompt"]


async def test_old_terminal_rows_are_pruned_on_restore(monkeypatch):
    job = await _enqueue(_ok_run())
    await _finish(job)
    con = sqlite3.connect(str(cj._db_path()))
    con.execute("UPDATE create_jobs SET updated_at='2000-01-01 00:00:00'")
    con.commit()
    con.close()
    _restart(monkeypatch)
    assert cj.get_job(job.id) is None
    assert _db_rows() == {}


def test_update_sidecar_import_kept():
    # create_jobs использует update_sidecar из generation_storage (не меняли)
    assert update_sidecar is cj.update_sidecar
