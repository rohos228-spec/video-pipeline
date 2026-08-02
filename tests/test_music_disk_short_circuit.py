"""generate_music accepts pre-seeded music/*.mp3 without Outsee."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.steps.generate_music import _find_music_on_disk


def test_find_music_on_disk_prefers_newest(tmp_path: Path) -> None:
    music = tmp_path / "music"
    music.mkdir()
    older = music / "music_old.mp3"
    newer = music / "music_new.mp3"
    older.write_bytes(b"x" * 2000)
    newer.write_bytes(b"y" * 2000)
    # bump mtime of newer
    import os
    import time

    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now, now))
    found = _find_music_on_disk(music)
    assert found == newer


def test_find_music_on_disk_empty(tmp_path: Path) -> None:
    assert _find_music_on_disk(tmp_path / "missing") is None
