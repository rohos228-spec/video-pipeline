"""Montage must follow absolute R15 timestamps (with gaps), not concat-from-zero."""

from pathlib import Path

from app.services.frame_audio import FrameAudioClip
from app.services.plan_timestamps import r15_voice_diff_lines
from app.services.shot2_montage import build_video_clip_specs
from app.services.whisper import WordTS


class _Fr:
    def __init__(self, number: int) -> None:
        self.number = number


def test_montage_inserts_gap_before_first_frame_start(tmp_path: Path) -> None:
    shot = tmp_path / "clip_001_a.mp4"
    shot.write_bytes(b"x")

    class _Project:
        id = 15

        class _Data:
            def __truediv__(self, other: str) -> Path:
                return tmp_path / other

        data_dir = _Data()

    audio_clips = [
        FrameAudioClip(1, tmp_path / "v.mp3", "a", 3.28, 5.76, 2.48),
        FrameAudioClip(2, tmp_path / "v.mp3", "b", 5.76, 8.72, 2.96),
    ]
    specs = build_video_clip_specs(
        _Project(),
        frames=[_Fr(1), _Fr(2)],
        audio_clips=audio_clips,
        primary_paths={1: shot, 2: shot},
    )
    assert abs(specs[0].timeline_start - 3.28) < 0.02
    assert abs(specs[0].duration - 2.48) < 0.02
    assert abs(specs[1].timeline_start - 5.76) < 0.02


def test_r15_voice_diff_detects_excel_start_mismatch() -> None:
    cells = [(1, "один два"), (2, "три четыре")]
    ts_cells = [(1, "0:00.00-0:05.76"), (2, "0:05.76-0:08.72")]
    words = [
        WordTS("один", 3.28, 4.0, 1.0),
        WordTS("два", 4.0, 5.76, 1.0),
        WordTS("три", 5.76, 6.5, 1.0),
        WordTS("четыре", 6.5, 8.72, 1.0),
    ]
    clips = [
        FrameAudioClip(1, Path("v.mp3"), "a", 0.0, 5.76, 5.76),
        FrameAudioClip(2, Path("v.mp3"), "b", 5.76, 8.72, 2.96),
    ]
    # Без audio_duration map_frames берёт start/end напрямую из words.
    lines = r15_voice_diff_lines(
        clips=clips,
        ts_cells=ts_cells,
        cells=cells,
        words=words,
        master=8.72,
        threshold=1.0,
    )
    assert any("кадр 1" in line for line in lines)
