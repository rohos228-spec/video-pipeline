"""list_projects передаёт batch_* в sync_projects — сигнатура должна принимать их."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.sidebar_layout import load_layout, sync_projects


@pytest.fixture()
def layout_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", data)
    return data


def test_sync_projects_accepts_batch_kwargs(layout_tmp: Path) -> None:
    sync_projects(
        {1, 2},
        batch_subprojects={3: (10, 1)},
        batch_names={10: "Batch A"},
    )
    layout = load_layout().get("project_layout") or {}
    assert "1" in layout
    assert "2" in layout
    # batch child не кладём в корень без batch-folder helpers
    assert "3" not in layout


def test_sync_projects_still_works_without_kwargs(layout_tmp: Path) -> None:
    sync_projects({5})
    layout = load_layout().get("project_layout") or {}
    assert layout["5"]["folder_id"] is None
