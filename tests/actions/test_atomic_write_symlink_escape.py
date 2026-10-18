"""old/ и .lock не должны быть symlink наружу из allowlist."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.orchestrator.node_store.atomic_write import atomic_write_text
from app.settings import settings
from tests.actions.test_whitelist_no_exec import analyze_module_source


def _symlink_or_skip(link: Path, target: Path, **kwargs) -> None:
    try:
        link.symlink_to(target, **kwargs)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("WinError 1314: нет привилегии на symlink")
        raise


def test_old_and_lock_symlinks_cannot_escape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    root = Path(settings.data_dir) / "node_overrides"
    root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = root / f"esc-{tmp_path.name}.json"
    dest.write_text('{"version": 0, "text": "seed"}', encoding="utf-8")
    old = root / "old"
    saved = root / f"old-saved-{tmp_path.name}"
    if old.is_symlink():
        old.unlink()
    elif old.exists():
        old.rename(saved)
    outside_old = outside / "via-old"
    outside_old.mkdir()
    _symlink_or_skip(old, outside_old, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="symlink|allowlist"):
            atomic_write_text(dest, "leaked", expected_version=0)
        assert list(outside_old.iterdir()) == []
        assert "leaked" not in dest.read_text(encoding="utf-8")
    finally:
        if old.is_symlink() or old.exists():
            old.unlink()
        if saved.exists():
            saved.rename(old)
        dest.unlink(missing_ok=True)
        lock = dest.with_name(dest.name + ".lock")
        if lock.is_symlink() or lock.exists():
            lock.unlink()

    outside_lock = outside / "via-lock"
    fresh = root / f"lock-{tmp_path.name}.json"
    lock = fresh.with_name(fresh.name + ".lock")
    if lock.is_symlink() or lock.exists():
        lock.unlink()
    _symlink_or_skip(lock, outside_lock)
    try:
        with pytest.raises(ValueError, match="symlink|allowlist"):
            atomic_write_text(fresh, "leaked", expected_version=0)
        assert not outside_lock.exists()
        assert not fresh.exists()
    finally:
        if lock.is_symlink() or lock.exists():
            lock.unlink()
        fresh.unlink(missing_ok=True)

    source = (
        "import os\n"
        "from pathlib import Path\n"
        "def save(src):\n"
        "    os.symlink(src, 'data/node_overrides/x.json')\n"
        "    os.link(src, 'data/node_overrides/y.json')\n"
        "    Path('data/node_overrides/z.json').symlink_to(src)\n"
        "    Path('data/node_overrides/w.json').hardlink_to(src)\n"
    )
    hits = analyze_module_source(source, "node.set_config")
    assert [hit.kind for hit in hits] == [
        "os.symlink",
        "os.link",
        "Path.symlink_to",
        "Path.hardlink_to",
    ]
    assert all(hit.allowed is False for hit in hits)

    with pytest.raises(ValueError, match="allowlist|old|lock|tmp"):
        atomic_write_text(root / "old" / "x.json", "no", expected_version=0)
    with pytest.raises(ValueError, match="allowlist|old|lock|tmp"):
        atomic_write_text(root / "x.lock", "no", expected_version=0)
    with pytest.raises(ValueError, match="allowlist|old|lock|tmp"):
        atomic_write_text(root / "x.tmp", "no", expected_version=0)
    with pytest.raises(ValueError, match="allowlist|корню"):
        atomic_write_text(root, "no", expected_version=0)
    shutil.rmtree(outside, ignore_errors=True)
