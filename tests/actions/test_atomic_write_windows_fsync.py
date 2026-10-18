"""Windows-путь синхронизирует файл бэкапа и tmp, но не каталог."""

from __future__ import annotations

import errno
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
    readonly: set[int] = set()
    real_open = os.open
    real_fsync = os.fsync
    real_replace = os.replace

    def tracked_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        acc = getattr(os, "O_ACCMODE", os.O_RDONLY | os.O_WRONLY | os.O_RDWR)
        if (flags & acc) == os.O_RDONLY:
            readonly.add(fd)
        return fd

    def fsync(fd: int) -> None:
        if fd in readonly:
            raise OSError(errno.EBADF, os.strerror(errno.EBADF))
        order.append("fsync")
        real_fsync(fd)

    def replace(src, dst, *args, **kwargs):
        order.append("replace")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(mod.os, "open", tracked_open)
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


def test_write_without_posix_open_flags(tmp_path: Path, monkeypatch) -> None:
    """Нет O_ACCMODE/O_NOFOLLOW/O_DIRECTORY — запись всё равно проходит."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    for name in ("O_ACCMODE", "O_NOFOLLOW", "O_DIRECTORY"):
        monkeypatch.delattr(os, name, raising=False)

    def publish(filename: str) -> None:
        dest = tmp_path / "node_overrides" / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        mod.atomic_write_text(dest, "next", expected_version=0)
        assert "next" in dest.read_text(encoding="utf-8")

    if os.name == "posix":
        monkeypatch.setattr(mod, "_posix_dir_fd", lambda: True)
        publish("posix.json")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod, "_posix_dir_fd", lambda: False)

    class Msvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def locking(self, fd: int, mode: int, length: int) -> None:
            return None

    monkeypatch.setitem(sys.modules, "msvcrt", Msvcrt())
    publish("win.json")


def test_windows_fsync_module_collects_without_fcntl(monkeypatch) -> None:
    """На Windows модуля fcntl нет: файл теста должен импортироваться."""
    import builtins
    import importlib.util

    real_import = builtins.__import__

    def guarded(modname, globals=None, locals=None, fromlist=(), level=0):
        if modname == "fcntl":
            raise ModuleNotFoundError("No module named 'fcntl'")
        return real_import(modname, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded)
    spec = importlib.util.spec_from_file_location(
        "vp_fsync_nofcntl",
        Path(__file__),
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "test_windows_fsync_requires_writable_fd")


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
