"""Вызовы #342, без которых данные тихо теряются. Каждый сайт — отдельная проверка."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fn(path: str, name: str) -> str:
    src = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    return ast.get_source_segment(src, fn) or ""


def test_delete_montage_frame_remembers_deleted_on_both_paths() -> None:
    text = _fn("app/services/montage_board_frames.py", "delete_montage_frame")
    assert text.count("remember_deleted(project, number)") == 2


def test_delete_coverage_child_remembers_deleted() -> None:
    text = _fn("app/services/montage_coverage_ops.py", "delete_coverage_child")
    assert "remember_deleted(project, int(frame.number))" in text


def test_patch_project_pushes_display_fields() -> None:
    text = _fn("app/web/routers/projects.py", "patch_project")
    assert "push_display_fields_to_master" in text


def test_upload_xlsx_forgets_tombstones() -> None:
    text = _fn("app/web/routers/project_ops.py", "upload_xlsx")
    assert "forget_deleted(p)" in text


def test_gpt_operator_patch_syncs_project_row() -> None:
    text = _fn("app/web/routers/project_ops.py", "gpt_operator_patch")
    assert "sync_project_row_to_master_db" in text


def test_project_asr_words_reads_master_scope() -> None:
    text = _fn("app/web/routers/project_ops.py", "project_asr_words")
    assert "session_scope" in text


def test_montage_board_delete_frame_refuses_when_busy() -> None:
    text = _fn("app/web/routers/project_ops.py", "montage_board_delete_frame")
    assert "_refuse_if_project_busy(p)" in text


def test_load_frame_prompt_rows_uses_project_db_path() -> None:
    text = _fn("app/services/agent_harness.py", "_load_frame_prompt_rows")
    assert "_db_path(data_dir=data_dir, project_id=project_id)" in text
    assert "_frames_db_path" not in text


def test_enrich_run_asks_allows_qc_noop() -> None:
    text = _fn("app/orchestrator/steps/enrich_xlsx.py", "run")
    assert "allows_qc_noop(" in text


def test_remember_deleted_roundtrip() -> None:
    from types import SimpleNamespace

    from app.services.frame_tombstones import deleted_numbers, forget_deleted, remember_deleted

    project = SimpleNamespace(meta={})
    remember_deleted(project, 7)
    assert deleted_numbers(project) == {7}
    forget_deleted(project)
    assert deleted_numbers(project) == set()
