"""Таймаут msvcrt не должен затираться unlock и оставлять fd открытым."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.orchestrator.node_store import atomic_write as mod
from app.orchestrator.node_store.atomic_write import VersionConflict
from app.settings import settings


def test_windows_lock_timeout_keeps_timeout_and_closes_fd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(mod, "_posix_dir_fd", lambda: False)
    closed: list[int] = []
    real_close = mod.os.close

    def spy_close(fd: int) -> None:
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(mod.os, "close", spy_close)

    class Msvcrt:
        LK_NBLCK = 1
        LK_UNLCK = 2

        def locking(self, fd: int, mode: int, length: int) -> None:
            if mode == self.LK_UNLCK:
                raise PermissionError("unlock without lock")
            raise OSError("busy")

    monkeypatch.setitem(sys.modules, "msvcrt", Msvcrt())
    dest = tmp_path / "node_overrides" / "a.json"
    dest.parent.mkdir(parents=True)
    with pytest.raises(TimeoutError):
        mod.atomic_write_text(dest, "x", expected_version=0, lock_timeout=0.05)
    assert closed
    assert not dest.exists()


def test_cleanup_error_does_not_mask_version_conflict(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    def boom(_fd: int) -> None:
        raise PermissionError("unlock failed")

    monkeypatch.setattr(mod, "_release_lock", boom)
    dest = tmp_path / "node_overrides" / "mask.json"
    dest.parent.mkdir(parents=True)
    dest.write_text('{"version": 1, "text": "a"}', encoding="utf-8")
    with pytest.raises(VersionConflict):
        mod.atomic_write_text(dest, "next", expected_version=0)
    assert dest.read_text(encoding="utf-8").startswith('{"version": 1')
