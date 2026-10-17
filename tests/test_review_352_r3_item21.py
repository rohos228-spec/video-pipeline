"""Ревью #352 п.21: ответ проекта A не применяется к проекту B."""

from pathlib import Path

SRC = Path("web/src/components/canvas/nii67-card-panel.tsx").read_text(encoding="utf-8")


def test_project_switch_bumps_generation_and_checks_id() -> None:
    assert "editGen.current = 0" not in SRC
    assert "editGen.current += 1" in SRC
    assert "pid !== projectRef.current" in SRC
    assert "projectRef" in SRC
