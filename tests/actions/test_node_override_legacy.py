"""Старый файл node_overrides читается как есть и мигрирует с версии 0."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.orchestrator.node_store.atomic_write import (
    VersionConflict,
    atomic_write_text,
    read_override,
)
from app.settings import settings


def test_legacy_override_reads_raw_and_migrates_from_zero(tmp_path: Path) -> None:
    dest = Path(settings.data_dir) / "node_overrides" / f"legacy-{tmp_path.name}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    raw = '{"role": "writer"}'
    dest.write_text(raw, encoding="utf-8")
    backup = dest.parent / "old" / f"{dest.name}.v0"
    try:
        assert read_override(dest) == raw
        with pytest.raises(VersionConflict) as exc:
            atomic_write_text(dest, "next", expected_version=1)
        assert exc.value.status_code == 409
        assert dest.read_text(encoding="utf-8") == raw
        atomic_write_text(dest, "wrapped", expected_version=0)
        assert backup.read_bytes() == raw.encode("utf-8")
        assert json.loads(dest.read_text(encoding="utf-8")) == {
            "version": 1,
            "text": "wrapped",
        }
        assert read_override(dest) == "wrapped"
    finally:
        dest.unlink(missing_ok=True)
        dest.with_name(dest.name + ".lock").unlink(missing_ok=True)
        for path in (dest.parent / "old").glob(dest.name + "*"):
            path.unlink()
