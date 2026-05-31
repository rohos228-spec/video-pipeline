"""Тесты: stale running → done, не pending."""

from __future__ import annotations

from app.models import NodeRunStatus, ProjectStatus
from app.services.run_sync import _infer_stale_running_node_status


def test_stale_running_plan_becomes_done_at_plan_ready() -> None:
    assert (
        _infer_stale_running_node_status("plan", ProjectStatus.plan_ready)
        == NodeRunStatus.done
    )


def test_stale_running_script_becomes_done_at_script_ready() -> None:
    assert (
        _infer_stale_running_node_status("script", ProjectStatus.script_ready)
        == NodeRunStatus.done
    )


def test_stale_running_plan_done_when_project_on_script_ready() -> None:
    assert (
        _infer_stale_running_node_status("plan", ProjectStatus.script_ready)
        == NodeRunStatus.done
    )


def test_stale_running_split_pending_at_script_ready() -> None:
    assert (
        _infer_stale_running_node_status("split", ProjectStatus.script_ready)
        == NodeRunStatus.pending
    )


def test_stale_running_next_step_pending_at_ready() -> None:
    assert (
        _infer_stale_running_node_status("script", ProjectStatus.plan_ready)
        == NodeRunStatus.pending
    )
