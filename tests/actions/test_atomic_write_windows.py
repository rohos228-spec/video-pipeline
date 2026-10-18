"""На Windows модуль импортируется без fcntl и берёт блокировку через msvcrt."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile

import pytest


def test_windows_import_and_msvcrt_lock(monkeypatch, tmp_path) -> None:
    name = "app.orchestrator.node_store.atomic_write"
    importlib.import_module(name)
    original = sys.modules[name]
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "fcntl", None)
    sys.modules.pop(name)
    try:
        mod = importlib.import_module(name)
        calls: list[tuple[int, int, int]] = []

        class Msvcrt:
            LK_NBLCK = 1
            LK_UNLCK = 2

            def locking(self, fd: int, mode: int, length: int) -> None:
                calls.append((fd, mode, length))

        fake = Msvcrt()
        monkeypatch.setitem(sys.modules, "msvcrt", fake)
        fd, path = tempfile.mkstemp(dir=tmp_path)
        os.close(fd)
        fd = os.open(path, os.O_RDWR)
        try:
            mod._acquire_lock(fd, timeout=1.0)
            assert calls
            assert calls[0][1] == fake.LK_NBLCK

            def busy(fd: int, mode: int, length: int) -> None:
                raise OSError("busy")

            fake.locking = busy
            with pytest.raises(TimeoutError):
                mod._acquire_lock(fd, timeout=0.05)
        finally:
            os.close(fd)
    finally:
        sys.modules[name] = original
