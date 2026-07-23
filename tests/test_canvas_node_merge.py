"""Позиции нод: merge предпочитает source (canvas_graph), не stale UI state."""

from __future__ import annotations

from pathlib import Path


def test_merge_helper_source_positions_win() -> None:
    """Зеркало web/src/lib/canvas-node-merge.ts — source.position побеждает."""
    src = Path("web/src/lib/canvas-node-merge.ts").read_text(encoding="utf-8")
    assert "position: n.position" in src
    assert "Позиции ВСЕГДА из source" in src or "position: n.position" in src
    # Не должно залипать на old.position как единственный источник.
    assert "position: old.position" not in src


def test_flow_canvas_waits_for_project_meta() -> None:
    src = Path("web/src/components/canvas/flow-canvas.tsx").read_text(encoding="utf-8")
    assert "project.isFetched" in src
    assert "fitView={!canvasGraph?.saved_at}" in src
    assert "mergeGraphNodesWithRuntime" in src
    assert "position: old.position" not in src
