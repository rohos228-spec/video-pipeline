"""Тесты строгой FSM статусов нод (NodeRunStatus)."""

from __future__ import annotations

import pytest

from app.models import NodeRun, NodeRunStatus
from app.services.node_status_machine import (
    apply_sync_target,
    is_transition_allowed,
    queue_node_for_start,
    reset_node_to_pending,
    start_node_running,
    transition_node_status,
)


def _nr(status: NodeRunStatus) -> NodeRun:
    return NodeRun(
        workflow_run_id=1,
        node_key="n_plan",
        node_type="plan",
        status=status,
    )


def test_allowed_pending_to_queued_to_running_to_done() -> None:
    nr = _nr(NodeRunStatus.pending)
    assert queue_node_for_start(nr, project_id=1, initiator="api")
    assert nr.status == NodeRunStatus.queued
    assert start_node_running(nr, project_id=1, initiator="api")
    assert nr.status == NodeRunStatus.running
    assert transition_node_status(
        nr, NodeRunStatus.done, initiator="worker", project_id=1
    )
    assert nr.status == NodeRunStatus.done


def test_allowed_failed_to_queued_retry() -> None:
    nr = _nr(NodeRunStatus.failed)
    nr.error = "timeout"
    assert transition_node_status(
        nr, NodeRunStatus.queued, initiator="api", project_id=1
    )
    assert nr.status == NodeRunStatus.queued


def test_allowed_running_to_failed() -> None:
    nr = _nr(NodeRunStatus.running)
    assert transition_node_status(
        nr,
        NodeRunStatus.failed,
        initiator="worker",
        project_id=1,
        error="boom",
    )
    assert nr.status == NodeRunStatus.failed
    assert nr.error == "boom"


def test_reset_any_to_pending() -> None:
    nr = _nr(NodeRunStatus.done)
    assert reset_node_to_pending(nr, project_id=1, initiator="api_reset")
    assert nr.status == NodeRunStatus.pending
    assert nr.finished_at is None


def test_forbidden_pending_to_done_without_running() -> None:
    nr = _nr(NodeRunStatus.pending)
    assert not transition_node_status(
        nr, NodeRunStatus.done, initiator="sync", project_id=1
    )
    assert nr.status == NodeRunStatus.pending


def test_checkpoint_backfill_pending_to_done() -> None:
    nr = _nr(NodeRunStatus.pending)
    assert apply_sync_target(
        nr,
        NodeRunStatus.done,
        project_id=1,
        checkpoint_upstream=True,
    )
    assert nr.status == NodeRunStatus.done


def test_forbidden_running_to_pending_auto_rollback() -> None:
    nr = _nr(NodeRunStatus.running)
    assert not transition_node_status(
        nr, NodeRunStatus.pending, initiator="sync", project_id=1
    )
    assert nr.status == NodeRunStatus.running


def test_stop_allows_running_to_pending() -> None:
    nr = _nr(NodeRunStatus.running)
    assert transition_node_status(
        nr, NodeRunStatus.pending, initiator="api_stop", project_id=1
    )
    assert nr.status == NodeRunStatus.pending


def test_forbidden_done_to_running() -> None:
    nr = _nr(NodeRunStatus.done)
    assert not transition_node_status(
        nr, NodeRunStatus.running, initiator="api", project_id=1
    )


@pytest.mark.parametrize(
    ("old", "new", "initiator", "backfill", "expected"),
    [
        (NodeRunStatus.pending, NodeRunStatus.queued, "api", False, True),
        (NodeRunStatus.queued, NodeRunStatus.running, "api", False, True),
        (NodeRunStatus.running, NodeRunStatus.done, "worker", False, True),
        (NodeRunStatus.running, NodeRunStatus.pending, "sync", False, False),
        (NodeRunStatus.done, NodeRunStatus.pending, "api_reset", False, True),
        (NodeRunStatus.failed, NodeRunStatus.queued, "api", False, True),
        (NodeRunStatus.pending, NodeRunStatus.done, "sync_checkpoint", True, True),
        (NodeRunStatus.pending, NodeRunStatus.done, "sync", False, False),
    ],
)
def test_is_transition_allowed_matrix(
    old: NodeRunStatus,
    new: NodeRunStatus,
    initiator: str,
    backfill: bool,
    expected: bool,
) -> None:
    assert (
        is_transition_allowed(
            old,
            new,
            initiator=initiator,
            allow_checkpoint_backfill=backfill,
        )
        is expected
    )
