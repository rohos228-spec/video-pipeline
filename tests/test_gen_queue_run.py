"""Tests for gen_queue_run target detection."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.services.gen_queue_run import (
    is_gen_queue_timeline_complete,
    ready_status_is_queue_target,
    status_at_or_past_target,
)


def _project(status: ProjectStatus, meta: dict | None = None) -> Project:
    return Project(slug="t", topic="t", status=status, auto_mode=True, meta=meta or {})


def test_until_images_stops_at_images_ready():
    project = _project(
        ProjectStatus.images_ready,
        meta={
            "gen_queue_run": {
                "mode": "until_node",
                "target_node_type": "images",
                "complete": False,
            }
        },
    )
    assert status_at_or_past_target(project, "images")
    assert is_gen_queue_timeline_complete(project)
    assert ready_status_is_queue_target(project, ProjectStatus.images_ready)


def test_until_images_not_done_while_generating():
    project = _project(
        ProjectStatus.generating_images,
        meta={
            "gen_queue_run": {
                "mode": "until_node",
                "target_node_type": "images",
                "complete": False,
            }
        },
    )
    assert not status_at_or_past_target(project, "images")
    assert not is_gen_queue_timeline_complete(project)


def test_full_mode_never_short_circuits():
    project = _project(
        ProjectStatus.plan_ready,
        meta={"gen_queue_run": {"mode": "full", "complete": False}},
    )
    assert not is_gen_queue_timeline_complete(project)
    assert not ready_status_is_queue_target(project, ProjectStatus.plan_ready)
