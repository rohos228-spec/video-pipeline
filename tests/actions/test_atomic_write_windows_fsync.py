"""Windows-путь синхронизирует файл бэкапа и tmp, но не каталог."""

from __future__ import annotations

import errno
import fcntl
import os
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


def test_windows_fsync_requires_writable_fd(tmp_path: Path, monkeypatch) -> None:
    """os.fsync на read-only fd на Windows — EBADF. Писать и синхронизировать один fd."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod, "_posix_dir_fd", lambda: False)

    class Msvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def locking(self, fd: int, mode: int, length: int) -> None:
            return None

    monkeypatch.setitem(sys.modules, "msvcrt", Msvcrt())
    order: list[str] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def fsync(fd: int) -> None:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        if (flags & os.O_ACCMODE) == os.O_RDONLY:
            raise OSError(errno.EBADF, os.strerror(errno.EBADF))
        order.append("fsync")
        real_fsync(fd)

    def replace(src, dst, *args, **kwargs):
        order.append("replace")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(mod.os, "fsync", fsync)
    monkeypatch.setattr(mod.os, "replace", replace)
    dest = tmp_path / "node_overrides" / "sync.json"
    dest.parent.mkdir(parents=True)
    dest.write_text('{"version": 0, "text": "seed"}', encoding="utf-8")
    mod.atomic_write_text(dest, "next", expected_version=0)
    assert order
    assert "fsync" in order and "replace" in order
    assert order.index("fsync") < order.index("replace")
    assert "next" in dest.read_text(encoding="utf-8")


def test_tmp_realpath_is_checked_before_write(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod, "_posix_dir_fd", lambda: False)

    class Msvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def locking(self, fd: int, mode: int, length: int) -> None:
            return None

    monkeypatch.setitem(sys.modules, "msvcrt", Msvcrt())
    seen: list[bool] = []
    real = mod._assert_realpath_inside

    def spy(path: Path, root: Path) -> None:
        if ".tmp" in Path(path).name:
            seen.append(Path(path).exists())
        real(path, root)

    monkeypatch.setattr(mod, "_assert_realpath_inside", spy)
    dest = tmp_path / "node_overrides" / "order.json"
    dest.parent.mkdir(parents=True)
    mod.atomic_write_text(dest, "next", expected_version=0)
    assert seen == [False]
