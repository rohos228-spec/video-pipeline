"""П.6: saveFrames не пишет кадры проекта A в редактор B."""

from __future__ import annotations

from pathlib import Path


def test_save_frames_checks_project_after_pending() -> None:
    text = Path("web/src/components/canvas/nii67-card-panel.tsx").read_text(encoding="utf-8")
    start = text.index("const saveFrames = useMutation")
    end = text.index("const makePlan = useMutation")
    block = text[start:end]
    assert "const pid = projectId" in block
    assert "putNii67Frames(pid" in block
    assert "pid !== projectRef.current" in block
