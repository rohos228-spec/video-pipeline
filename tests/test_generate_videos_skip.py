"""Повторный запуск шага video не должен пропускать кадры без файла клипа."""

from pathlib import Path

from app.models import Frame, FrameStatus
from app.orchestrator.steps.generate_videos import (
    _skip_frame_video_generation,
    resolve_scene_image_path,
)


def _frame(status: FrameStatus) -> Frame:
    return Frame(id=1, project_id=1, number=1, status=status)


def test_video_generated_without_file_not_skipped() -> None:
    fr = _frame(FrameStatus.video_generated)
    assert _skip_frame_video_generation(fr, has_video_file=False) is False


def test_video_generated_with_file_skipped() -> None:
    fr = _frame(FrameStatus.video_generated)
    assert _skip_frame_video_generation(fr, has_video_file=True) is True


def test_video_approved_always_skipped() -> None:
    fr = _frame(FrameStatus.video_approved)
    assert _skip_frame_video_generation(fr, has_video_file=False) is True


def test_resolve_scene_image_falls_back_to_disk(tmp_path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    png = scenes / "frame_007_ea970ac6.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 250_000)
    stale = str(scenes / "frame_007_s2_760fbcbb.png")
    got = resolve_scene_image_path(
        artifact_path=stale,
        scenes_dir=scenes,
        frame_number=7,
    )
    assert got == png


def test_resolve_scene_image_rejects_s2_even_when_file_exists(tmp_path) -> None:
    """Регрессия: shot_01 video не должен брать frame_*_s2_*.png из артефакта БД."""
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    shot1 = scenes / "frame_007_ea970ac6.png"
    shot1.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 250_000)
    shot2 = scenes / "frame_007_s2_760fbcbb.png"
    shot2.write_bytes(b"\x89PNG\r\n\x1a\n" + b"y" * 250_000)
    got = resolve_scene_image_path(
        artifact_path=str(shot2),
        scenes_dir=scenes,
        frame_number=7,
    )
    assert got == shot1
    assert "_s2_" not in got.name


def test_effective_shot_from_artifact_path_wins_over_meta() -> None:
    from app.services.plan_shot2 import effective_shot_from_artifact

    path = Path("clip_001_s2_abc.mp4")
    assert effective_shot_from_artifact({"shot": 1}, path) == 2
