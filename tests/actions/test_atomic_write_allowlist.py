"""Назначение atomic_write_text только внутри reports и node_overrides."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.node_store.atomic_write import atomic_write_text
from app.settings import settings


def _cleanup(path: Path) -> None:
    if path.is_symlink() or path.exists():
        path.unlink()
    lock = path.with_name(path.name + ".lock")
    if lock.exists():
        lock.unlink()


def test_atomic_write_rejects_outside_allowlist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    outside = tmp_path / "x.json"
    with pytest.raises(ValueError, match="allowlist"):
        atomic_write_text(outside, "no", expected_version=0)
    assert not outside.exists()

    root = Path(settings.data_dir) / "node_overrides"
    root.mkdir(parents=True, exist_ok=True)
    escaped = root / ".." / ".." / f"escaped-{tmp_path.name}.json"
    with pytest.raises(ValueError, match="allowlist"):
        atomic_write_text(escaped, "no", expected_version=0)
    assert not escaped.resolve().exists()

    link = root / f"link-{tmp_path.name}.json"
    secret = tmp_path / "secret.json"
    _cleanup(link)
    link.symlink_to(secret)
    try:
        with pytest.raises(ValueError, match="allowlist|symlink"):
            atomic_write_text(link, "no", expected_version=0)
        assert not secret.exists()
    finally:
        _cleanup(link)

    ok = root / f"ok-{tmp_path.name}.json"
    _cleanup(ok)
    try:
        atomic_write_text(ok, "yes", expected_version=0)
        assert "yes" in ok.read_text(encoding="utf-8")
    finally:
        _cleanup(ok)
        old = root / "old" / f"{ok.name}.v0"
        if old.exists():
            old.unlink()
