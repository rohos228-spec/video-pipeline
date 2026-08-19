"""Жёсткое применение лимитов символов после GPT-разбивки."""

from __future__ import annotations

from app.models import Project
from app.services.node_step_params import (
    split_cell_limits_from_project,
    split_params_fingerprint,
)
from app.services.run_sync import _node_already_succeeded_for_project
from app.models import NodeRun, NodeRunStatus, ProjectStatus
from app.services.voiceover_split_local import (
    enforce_split_cell_limits,
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


def test_enforce_merges_short_and_splits_long() -> None:
    blocks = [
        "короткий раз.",
        "ещё кусок.",
        "А" * 90,
    ]
    out = enforce_split_cell_limits(
        blocks, min_chars=20, max_chars=40, avg_min=25, avg_max=35
    )
    assert len(out) >= 2
    lens = [len(b) for b in out]
    assert min(lens) >= 20
    assert max(lens) <= 40
    assert "".join(out).replace(" ", "") == "".join(blocks).replace(" ", "")


def test_enforce_keeps_ok_blocks() -> None:
    blocks = ["x" * 30, "y" * 35, "z" * 28]
    out = enforce_split_cell_limits(
        blocks, min_chars=27, max_chars=54, avg_min=27, avg_max=52
    )
    assert out == blocks


def test_frames_spec_stats() -> None:
    spec = [{"закадр": "a" * 10}, {"закадр": "b" * 40}]
    st = frames_spec_cell_stats(spec)
    assert st["n"] == 2
    assert st["min"] == 10
    assert st["max"] == 40


def test_split_not_succeeded_without_split_completed() -> None:
    """Reconcile не должен красить n_split в done, если split_completed нет.

    Иначе после ▶ разбивки поверх живого enrich нода становится ✅ без GPT.
    """
    p = Project(topic="t", status=ProjectStatus.enrich_1_ready)
    p.meta = {}  # split_completed отсутствует
    nr = NodeRun(
        workflow_run_id=1,
        node_key="n_split",
        node_type="split",
        status=NodeRunStatus.running,
    )
    assert _node_already_succeeded_for_project(p, nr) is False

    p.meta = {"split_completed": True}
    assert _node_already_succeeded_for_project(p, nr) is True
