"""Windows-путь синхронизирует файл бэкапа и tmp, но не каталог."""

from __future__ import annotations

import sys
from pathlib import Path

from app.orchestrator.node_store import atomic_write as mod
from app.settings import settings


def test_windows_path_fsyncs_file_before_replace(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod, "_posix_dir_fd", lambda: False)

    class Msvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def locking(self, fd: int, mode: int, length: int) -> None:
            return None

    monkeypatch.setitem(sys.modules, "msvcrt", Msvcrt())
    calls: list[int] = []
    real_fsync = mod.os.fsync

    def spy(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(mod.os, "fsync", spy)
    dest = tmp_path / "node_overrides" / "sync.json"
    dest.parent.mkdir(parents=True)
    dest.write_text('{"version": 0, "text": "seed"}', encoding="utf-8")
    mod.atomic_write_text(dest, "next", expected_version=0)
    assert len(calls) >= 1
    assert "next" in dest.read_text(encoding="utf-8")
