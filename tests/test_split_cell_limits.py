"""Регрессии split: reconcile, params, fail-closed без auto-enforce rewrite."""

from __future__ import annotations

import pytest

from app.models import NodeRun, NodeRunStatus, Project, ProjectStatus
from app.services.node_step_params import (
    build_split_params_block,
    split_cell_limits_from_project,
    split_params_fingerprint,
)
from app.services.run_sync import _node_already_succeeded_for_project
from app.services.voiceover_split_local import (
    assert_frames_spec_within_limits,
    frames_spec_cell_stats,
)


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
    assert "отклонят" in block


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


def test_assert_limits_fail_closed() -> None:
    spec = [{"закадр": "короткий"}, {"закадр": "x" * 90}]
    with pytest.raises(RuntimeError, match="вне лимитов"):
        assert_frames_spec_within_limits(spec, min_chars=27, max_chars=54)


def test_assert_limits_ok() -> None:
    spec = [{"закадр": "a" * 30}, {"закадр": "b" * 40}]
    assert_frames_spec_within_limits(spec, min_chars=27, max_chars=54)
    st = frames_spec_cell_stats(spec)
    assert st["n"] == 2
    assert st["min"] == 30


def test_no_auto_enforce_rewrite_in_pipeline() -> None:
    """Регресс #33: нельзя тихо переписывать кадры enforce'ом (80→168)."""
    from pathlib import Path

    runners = Path("app/services/xlsx_step_runners.py").read_text(encoding="utf-8")
    frames = Path("app/orchestrator/steps/split_frames.py").read_text(encoding="utf-8")
    assert "enforce_split_cell_limits" not in runners
    assert "enforce_split_cell_limits" not in frames
    assert "assert_frames_spec_within_limits" in runners
    assert "local re-enforce" not in frames
