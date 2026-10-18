"""Повторное ревью #352 №19: хук ячеек живёт в каталоге проекта."""

from __future__ import annotations

from pathlib import Path

from app.services.apply_ops_batches import apply_only_cells_hook


def test_only_cells_ignores_cwd_file_without_flag(tmp_path: Path, monkeypatch) -> None:
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    (cwd / "_only_cells_node.json").write_text('["u1"]', encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    frames = [{"uuid": "u1"}, {"uuid": "u2"}]
    out = apply_only_cells_hook(project, 7, "node", frames, list(frames))
    assert [row["uuid"] for row in out] == ["u1", "u2"]
    assert (cwd / "_only_cells_node.json").is_file()


def test_only_cells_filters_project_file_when_flag_on(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VP_ONLY_CELLS", "1")
    project = tmp_path / "project"
    project.mkdir()
    hook = project / "_only_cells_7_node.json"
    hook.write_text('["u2"]', encoding="utf-8")
    frames = [{"uuid": "u1"}, {"uuid": "u2"}]
    out = apply_only_cells_hook(project, 7, "node", frames, list(frames))
    assert [row["uuid"] for row in out] == ["u2"]
    assert not hook.exists()
    assert (project / "_only_cells_7_node.json.used").is_file()
