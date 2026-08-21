"""Стражи: Excel только Import/Export, не mid-pipeline."""

from __future__ import annotations

import inspect
from pathlib import Path

from app.services import db_apply
from app.services import excel_io


def test_apply_ops_default_export_xlsx_false() -> None:
    sig = inspect.signature(db_apply.apply_ops)
    assert sig.parameters["export_xlsx"].default is False


def test_excel_io_exports_facade() -> None:
    assert excel_io.export_project_xlsx is db_apply.export_project_xlsx
    assert callable(excel_io.import_project_xlsx)


def test_orchestrator_steps_no_forbidden_excel_io() -> None:
    forbidden = (
        "write_plan_",
        "writeback_project_xlsx",
        "write_asr_timestamps_to_r15",
        "sync_project_xlsx",
        "sync_animation_prompts_from_xlsx",
        "apply_image_prompts_from_xlsx",
        "bootstrap_frames_for_image_step",
    )
    root = Path("app/orchestrator/steps")
    offenders: list[str] = []
    for p in sorted(root.glob("*.py")):
        text = p.read_text(encoding="utf-8")
        for bad in forbidden:
            if bad in text:
                offenders.append(f"{p.as_posix()}: {bad}")
    assert not offenders, "forbidden Excel I/O in steps:\n" + "\n".join(offenders)


def test_project_steps_no_auto_excel_sync() -> None:
    import app.services.project_steps as ps

    src = inspect.getsource(ps)
    assert "sync_project_xlsx" not in src
    assert "sync_animation_prompts_from_xlsx" not in src
    assert "bootstrap_frames_for_image_step" not in src
