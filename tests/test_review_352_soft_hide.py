"""П.1: скрытый кадр не удаляется и не попадает в платную перегенерацию."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.models import FrameStatus
from app.orchestrator.steps.generate_images import (
    frame_hidden_from_paid_generation,
    requeue_failed_frames_without_png,
)


def test_requeue_skips_hidden_frame(tmp_path: Path) -> None:
    hidden = SimpleNamespace(
        number=2,
        status=FrameStatus.failed,
        attrs={"активен": False, "удалён": True},
    )
    live = SimpleNamespace(number=3, status=FrameStatus.failed, attrs={})
    n = requeue_failed_frames_without_png([hidden, live], tmp_path, project_id=1)
    assert frame_hidden_from_paid_generation(hidden) is True
    assert hidden.status is FrameStatus.failed
    assert live.status is FrameStatus.image_prompt_ready
    assert n == 1
