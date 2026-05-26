"""Чтение закадрового текста с листа «план» (v8)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openpyxl import Workbook

from app.models import Project
from app.services.animation_prompt_gpt import voiceover_for_frame
from app.services.xlsx_v8_import import ROW_VOICEOVER_V8, SHEET_PLAN_V8
from app.storage.plan_sheet_v8 import plan_frame_column, read_plan_voiceover


def test_read_plan_voiceover_from_xlsx(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "data"
    slug_dir = root / "videos" / "t1"
    slug_dir.mkdir(parents=True)
    xlsx = slug_dir / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_PLAN_V8
    col = plan_frame_column(1)
    ws.cell(row=ROW_VOICEOVER_V8, column=col, value="Текст кадра один")
    wb.save(xlsx)

    monkeypatch.setattr("app.models.settings.data_dir", root)
    project = Project(topic="t", slug="t1")

    assert read_plan_voiceover(project, 1) == "Текст кадра один"
    fr = SimpleNamespace(number=1, voiceover_text=None)
    assert voiceover_for_frame(project, fr) == "Текст кадра один"
