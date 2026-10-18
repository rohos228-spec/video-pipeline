"""Junction на old/ не должен уводить бэкап за allowlist."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

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


def _make_old_junction(old: Path, outside: Path, monkeypatch, *, windows: bool) -> None:
    if windows:
        import _winapi

        _winapi.CreateJunction(str(old), str(outside))
        return
    old.mkdir()
    original = os.path.isjunction

    def fake(path) -> bool:
        if Path(path).name == "old":
            return True
        return original(path)

    monkeypatch.setattr(os.path, "isjunction", fake)


def test_old_junction_cannot_escape_windows(tmp_path: Path, monkeypatch) -> None:
    windows = sys.platform == "win32"
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _force_windows_writer(monkeypatch)
    root = tmp_path / "node_overrides"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = root / "a.json"
    dest.write_text('{"version": 0, "text": "seed"}', encoding="utf-8")
    old = root / "old"
    _make_old_junction(old, outside, monkeypatch, windows=windows)
    with pytest.raises(ValueError, match="junction|reparse|allowlist"):
        mod.atomic_write_text(dest, "leaked", expected_version=0)
    assert list(outside.iterdir()) == []
    assert "leaked" not in dest.read_text(encoding="utf-8")
