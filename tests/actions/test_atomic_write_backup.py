"""Копия old/<name>.vN не затирается, если номер версии повторился."""

from __future__ import annotations

import json
from pathlib import Path

from app.orchestrator.node_store.atomic_write import atomic_write_text
from app.settings import settings


def test_old_version_backup_not_overwritten(tmp_path: Path) -> None:
    dest = Path(settings.data_dir) / "node_overrides" / f"bak-{tmp_path.name}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup = dest.parent / "old" / f"{dest.name}.v1"
    try:
        atomic_write_text(dest, "first", expected_version=0)
        atomic_write_text(dest, "second", expected_version=1)
        assert "first" in backup.read_text(encoding="utf-8")
        dest.write_text(
            json.dumps({"version": 1, "text": "rebuilt"}, ensure_ascii=False),
            encoding="utf-8",
        )
        atomic_write_text(dest, "third", expected_version=1)
        assert "first" in backup.read_text(encoding="utf-8")
        extras = list((dest.parent / "old").glob(f"{dest.name}.v1.*"))
        assert extras
        assert any("rebuilt" in path.read_text(encoding="utf-8") for path in extras)
    finally:
        dest.unlink(missing_ok=True)
        dest.with_name(dest.name + ".lock").unlink(missing_ok=True)
        for path in (dest.parent / "old").glob(dest.name + "*"):
            path.unlink()
