"""Перед replace файл сбрасывается на диск, каталог тоже, права не 0600."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

from app.orchestrator.node_store import atomic_write as mod
from app.settings import settings


def _force_windows_writer(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod, "_posix_dir_fd", lambda: False)

    class Msvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def locking(self, fd: int, mode: int, length: int) -> None:
            return None

    monkeypatch.setitem(sys.modules, "msvcrt", Msvcrt())


def _fsync_kinds(monkeypatch, dest: Path, *, windows: bool) -> list[str]:
    if windows:
        _force_windows_writer(monkeypatch)
    kinds: list[str] = []
    real_fsync = mod.os.fsync

    def spy(fd: int) -> None:
        kind = "dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        kinds.append(kind)
        real_fsync(fd)

    monkeypatch.setattr(mod.os, "fsync", spy)
    mod.atomic_write_text(dest, "hello", expected_version=0)
    return kinds


def test_atomic_write_fsyncs_file_and_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    root = Path(settings.data_dir) / "node_overrides"
    root.mkdir(parents=True, exist_ok=True)

    def check(dest: Path, kinds: list[str], *, windows: bool) -> None:
        try:
            if windows:
                assert "file" in kinds
                assert kinds.count("dir") == 0
            else:
                assert len(kinds) >= 2
                assert "dir" in kinds
                assert "file" in kinds
            assert dest.stat().st_mode & 0o777 != 0o600
        finally:
            dest.unlink(missing_ok=True)
            dest.with_name(dest.name + ".lock").unlink(missing_ok=True)
            for path in (dest.parent / "old").glob(dest.name + "*"):
                path.unlink()

    if os.name == "posix":
        dest = root / f"sync-{tmp_path.name}.json"
        check(dest, _fsync_kinds(monkeypatch, dest, windows=False), windows=False)
    win_dest = root / f"sync-win-{tmp_path.name}.json"
    check(win_dest, _fsync_kinds(monkeypatch, win_dest, windows=True), windows=True)
