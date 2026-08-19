"""Регрессии split: reconcile + params в промпте (без post-GPT enforce)."""

from __future__ import annotations

from app.models import NodeRun, NodeRunStatus, Project, ProjectStatus
from app.services.node_step_params import (
    build_split_params_block,
    split_cell_limits_from_project,
    split_params_fingerprint,
)
from app.services.run_sync import _node_already_succeeded_for_project


def test_split_cell_limits_from_project() -> None:
    p = Project(topic="t")
    p.meta = {
        "node_step_params": {
            "split": {
                "cell_min_chars": 27,
                "cell_max_chars": 54,
                "cell_avg_min": 27,
                "cell_avg_max": 52,
            }
        }
    }
    lim = split_cell_limits_from_project(p)
    assert lim == (27, 54, 27, 52)
    assert split_params_fingerprint(p) == "27:54:27:52"
    block = build_split_params_block(p)
    assert "27" in block and "54" in block


def test_split_not_succeeded_without_split_completed() -> None:
    """Reconcile не красит n_split в done без split_completed."""
    p = Project(topic="t", status=ProjectStatus.enrich_1_ready)
    p.meta = {}
    nr = NodeRun(
        workflow_run_id=1,
        node_key="n_split",
        node_type="split",
        status=NodeRunStatus.running,
    )
    assert _node_already_succeeded_for_project(p, nr) is False

    p.meta = {"split_completed": True}
    assert _node_already_succeeded_for_project(p, nr) is True


def test_split_pipeline_has_no_post_gpt_enforce() -> None:
    """Split пишет GPT frames as-is — без enforce_split_cell_limits."""
    from pathlib import Path

    runners = Path("app/services/xlsx_step_runners.py").read_text(encoding="utf-8")
    frames = Path("app/orchestrator/steps/split_frames.py").read_text(encoding="utf-8")
    assert "enforce_split_cell_limits" not in runners
    assert "enforce_split_cell_limits" not in frames
    assert "после enforce" not in runners
