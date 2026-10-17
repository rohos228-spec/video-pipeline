"""Два писателя с одной версией: ровно один проходит, второй получает 409."""

from __future__ import annotations

import threading
from pathlib import Path

from app.orchestrator.node_store import atomic_write as mod
from app.settings import settings


def test_atomic_write_race_one_conflict(tmp_path: Path) -> None:
    dest = Path(settings.data_dir) / "node_overrides" / f"race-{tmp_path.name}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    for stale in (dest, dest.with_name(dest.name + ".lock")):
        if stale.exists():
            stale.unlink()
    backup_dir = dest.parent / "old"
    if backup_dir.is_dir():
        for stale in backup_dir.glob(dest.name + "*"):
            stale.unlink()

    original = mod._stored_version
    gate = threading.Event()
    entered = threading.Event()
    state = {"n": 0}
    state_lock = threading.Lock()

    def slow(target: Path) -> int:
        value = original(target)
        with state_lock:
            index = state["n"]
            state["n"] += 1
        if index == 0:
            entered.set()
            assert gate.wait(timeout=5)
        return value

    results: list[str] = []
    errors: list[str] = []
    box = threading.Lock()

    def worker(text: str) -> None:
        try:
            mod.atomic_write_text(dest, text, expected_version=0)
        except mod.VersionConflict:
            with box:
                errors.append(text)
            return
        with box:
            results.append(text)

    threads = [
        threading.Thread(target=worker, args=("alpha",)),
        threading.Thread(target=worker, args=("beta",)),
    ]
    mod._stored_version = slow
    try:
        for thread in threads:
            thread.start()
        assert entered.wait(timeout=5)
        gate.set()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
        assert len(results) == 1
        assert len(errors) == 1
        body = dest.read_text(encoding="utf-8")
        assert results[0] in body
        assert errors[0] not in body
    finally:
        gate.set()
        mod._stored_version = original
        for thread in threads:
            thread.join(timeout=2)
        if dest.exists():
            dest.unlink()
        lock_path = dest.with_name(dest.name + ".lock")
        if lock_path.exists():
            lock_path.unlink()
        if backup_dir.is_dir():
            for stale in backup_dir.glob(dest.name + "*"):
                stale.unlink()
