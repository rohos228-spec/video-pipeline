"""Повторный запуск шага video не должен пропускать кадры без файла клипа."""

from app.models import Frame, FrameStatus
from app.orchestrator.steps.generate_videos import _skip_frame_video_generation


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
