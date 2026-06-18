"""Равное деление mp3 по кадрам без текста."""

from pathlib import Path

from app.models import Frame, Project
from app.services.frame_audio import (
    _voiceover_cells_for_frames,
    frame_clips_equal_duration,
)


def test_voiceover_cells_from_frame_db() -> None:
    project = Project(slug="t", topic="x")
    frames = [
        Frame(project_id=1, number=1, voiceover_text="кадр один"),
        Frame(project_id=1, number=2, voiceover_text="кадр два"),
    ]
    cells = _voiceover_cells_for_frames(project, frames, [(1, ""), (2, "")])
    assert cells == [(1, "кадр один"), (2, "кадр два")]


def test_equal_duration_clips_cover_master() -> None:
    frames = [Frame(project_id=1, number=i) for i in range(1, 4)]
    clips = frame_clips_equal_duration(frames, 30.0, Path("voice.mp3"))
    assert len(clips) == 3
    assert clips[0].start_ts == 0.0
    assert clips[-1].end_ts == 30.0
    assert abs(sum(c.duration for c in clips) - 30.0) < 0.01
