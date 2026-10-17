"""Перед replace файл сбрасывается на диск, каталог тоже, права не 0600."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.node_store import atomic_write as mod
from app.settings import settings


def test_atomic_write_fsyncs_file_and_directory(tmp_path: Path, monkeypatch) -> None:
    calls: list[int] = []
    real_fsync = mod.os.fsync

    def spy(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(mod.os, "fsync", spy)
    dest = Path(settings.data_dir) / "node_overrides" / f"sync-{tmp_path.name}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        mod.atomic_write_text(dest, "hello", expected_version=0)
        assert len(calls) >= 2
        assert dest.stat().st_mode & 0o777 != 0o600
    finally:
        dest.unlink(missing_ok=True)
        dest.with_name(dest.name + ".lock").unlink(missing_ok=True)
        for path in (dest.parent / "old").glob(dest.name + "*"):
            path.unlink()
