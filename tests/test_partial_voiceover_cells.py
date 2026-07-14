"""Partial R49 cells fallback."""

from __future__ import annotations

from pathlib import Path

from app.models import Frame, Project
from app.services.frame_audio import _voiceover_cells_for_frames, frame_clips_from_whisper
from app.services.whisper import WordTS


def test_partial_r49_uses_voiceover_txt_split(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "data"
    slug_dir = root / "videos" / "t"
    slug_dir.mkdir(parents=True)
    blocks = [f"Блок номер {i} с достаточно длинным текстом для теста." for i in range(1, 6)]
    (slug_dir / "voiceover.txt").write_text("\n\n".join(blocks), encoding="utf-8")
    monkeypatch.setattr("app.models.settings.data_dir", root)
    project = Project(slug="t", topic="x")

    frames = [Frame(project_id=1, number=i) for i in range(1, 6)]
    cells = [(1, blocks[0]), (2, blocks[1]), (3, ""), (4, ""), (5, "")]
    out = _voiceover_cells_for_frames(project, frames, cells)
    assert len(out) == 5
    assert all(t for _, t in out)


def test_many_empty_frames_get_equal_duration(tmp_path: Path) -> None:
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"x")
    cells = [(i, "текст" if i <= 2 else "") for i in range(1, 11)]
    words = [WordTS(f"w{i}", float(i), float(i + 1), 1.0) for i in range(20)]
    clips = frame_clips_from_whisper(cells, words, master=100.0, voice_full_path=voice)
    assert len(clips) == 10
    fair = 10.0
    for c in clips:
        assert abs(c.duration - fair) < 1.5
