"""Ошибка чтения не становится версией 0. Не-JSON текст мигрирует."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.node_store.atomic_write import atomic_write_text
from app.settings import settings


def test_broken_json_is_not_version_zero(tmp_path: Path, monkeypatch) -> None:
    """Не-JSON текст — сырой старый формат, версия 0, байты уходят в old/."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    dest = tmp_path / "node_overrides" / "bad.json"
    dest.parent.mkdir(parents=True)
    raw = "{not-json"
    dest.write_text(raw, encoding="utf-8")
    atomic_write_text(dest, "next", expected_version=0)
    backup = dest.parent / "old" / f"{dest.name}.v0"
    assert backup.read_text(encoding="utf-8") == raw


def test_stored_version_read_error_is_not_version_zero(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    dest = tmp_path / "node_overrides" / "io.json"
    dest.parent.mkdir(parents=True)
    dest.write_text('{"version": 1, "text": "a"}', encoding="utf-8")
    real = Path.read_bytes

    def boom(self: Path, *args, **kwargs):
        if self == dest:
            raise OSError("read failed")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", boom)
    with pytest.raises(OSError, match="read failed"):
        atomic_write_text(dest, "next", expected_version=1)
