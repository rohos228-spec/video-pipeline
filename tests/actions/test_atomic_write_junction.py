"""Junction на old/ не должен уводить бэкап за allowlist."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from app.orchestrator.node_store import atomic_write as mod
from app.settings import settings
from tests.actions.test_whitelist_no_exec import analyze_module_source


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

        # src — существующая цель, dst — создаваемая ссылка. Иначе WinError 2.
        _winapi.CreateJunction(str(outside), str(old))
        return
    old.mkdir()
    original = os.path.isjunction

    def fake(path) -> bool:
        if Path(path).name == "old":
            return True
        return original(path)

    monkeypatch.setattr(os.path, "isjunction", fake)


def test_create_junction_args_are_target_then_link(tmp_path: Path, monkeypatch) -> None:
    """CreateJunction(src=цель, dst=ссылка). Обратный порядок — WinError 2."""
    calls: list[tuple[str, str]] = []
    winapi = types.ModuleType("_winapi")

    def CreateJunction(src: str, dst: str) -> None:
        calls.append((src, dst))

    winapi.CreateJunction = CreateJunction
    monkeypatch.setitem(sys.modules, "_winapi", winapi)
    old = tmp_path / "old"
    outside = tmp_path / "outside"
    outside.mkdir()
    _make_old_junction(old, outside, monkeypatch, windows=True)
    assert calls == [(str(outside), str(old))]


def test_create_junction_is_denied() -> None:
    source = (
        "import _winapi\n"
        "def save(src):\n"
        "    _winapi.CreateJunction(src, 'data/node_overrides/old')\n"
        "    link = _winapi.CreateJunction\n"
    )
    hits = analyze_module_source(source, "node.set_config")
    assert [hit.kind for hit in hits] == ["_winapi.CreateJunction", "_winapi.CreateJunction"]
    assert all(hit.allowed is False for hit in hits)


def test_dangling_old_junction_is_rejected(tmp_path: Path, monkeypatch) -> None:
    """Висячий junction: exists() ложен, lstat/isjunction всё равно видит reparse."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    _force_windows_writer(monkeypatch)
    root = tmp_path / "node_overrides"
    root.mkdir()
    dest = root / "a.json"
    dest.write_text('{"version": 0, "text": "seed"}', encoding="utf-8")
    original = getattr(os.path, "isjunction", None)

    def fake(path) -> bool:
        if Path(path).name == "old":
            return True
        if original is None:
            return False
        return original(path)

    monkeypatch.setattr(os.path, "isjunction", fake, raising=False)
    with pytest.raises(ValueError, match="junction|reparse|allowlist"):
        mod.atomic_write_text(dest, "leaked", expected_version=0)
    assert "leaked" not in dest.read_text(encoding="utf-8")


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
