"""storage_step_sync + meta helpers: свежий canvas после GPT."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.models import Project, ProjectStatus
from app.services.canvas_graph import find_canvas_node_key_by_type
from app.services.project_meta import set_meta_fields
from app.services.storage_node import list_stored_files
from app.services.storage_step_sync import sync_storage_after_step


def _project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(
        slug="p",
        topic="t",
        status=ProjectStatus.script_ready,
        hero_mode="no_hero",
        meta={},
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    return p


def test_find_canvas_node_key_by_type() -> None:
    meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_plan", "type": "plan"},
                {"id": "n_script", "type": "script"},
            ],
            "edges": [],
        }
    }
    assert find_canvas_node_key_by_type(meta, "script") == "n_script"
    assert find_canvas_node_key_by_type(meta, "missing") is None


def test_set_meta_fields_keeps_canvas_graph() -> None:
    p = Project(slug="x", topic="t", status=ProjectStatus.new, hero_mode="no_hero")
    p.meta = {
        "canvas_graph": {
            "nodes": [{"id": "n_script", "type": "script"}],
            "edges": [
                {
                    "id": "e1",
                    "source": "n_script",
                    "target": "n_storage",
                    "data": {"kind": "after"},
                }
            ],
        }
    }
    set_meta_fields(p, active_excel_gpt_node_key="n_excel")
    assert p.meta["active_excel_gpt_node_key"] == "n_excel"
    assert len(p.meta["canvas_graph"]["edges"]) == 1


def test_sync_storage_after_script_uses_canvas_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Даже без NodeRun — ключ из canvas_graph, voiceover → storage."""
    p = _project(tmp_path, monkeypatch)
    (p.data_dir / "voiceover.txt").write_text("Закадр " * 40, encoding="utf-8")
    script, store = "n_script", "n_storage_1"
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": script, "type": "script", "position": {"x": 0, "y": 0}},
                {"id": store, "type": "storage", "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": script,
                    "target": store,
                    "data": {"kind": "after"},
                }
            ],
        },
        "storage_nodes": {store: {"formats": ["any"], "autoSync": True}},
    }
    session = AsyncMock()
    session.refresh = AsyncMock()
    monkeypatch.setattr(
        "app.services.storage_step_sync.find_node_key_for_type",
        AsyncMock(return_value=None),
    )
    synced = asyncio.run(
        sync_storage_after_step(session, p, "script", log_prefix="make_script")
    )
    assert synced
    names = [f["originalName"] for f in list_stored_files(p, store)]
    assert "voiceover.txt" in names
