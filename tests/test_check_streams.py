"""check_streams 0..10 for vision batch parallelism."""

from __future__ import annotations

from app import settings as app_settings
from app.models import Project, ProjectStatus
from app.services.check_streams import (
    clamp_check_streams,
    get_check_streams,
    set_check_streams_meta,
)
from app.services.xlsx_step_runners import img_pr_live_streams


def test_clamp_check_streams() -> None:
    assert clamp_check_streams(-1) == 0
    assert clamp_check_streams(0) == 0
    assert clamp_check_streams(5) == 5
    assert clamp_check_streams(10) == 10
    assert clamp_check_streams(99) == 10


def test_get_check_streams_meta(monkeypatch) -> None:
    monkeypatch.setattr(app_settings.settings, "check_max_streams", 2)
    p = Project(slug="c", topic="t", status=ProjectStatus.enrich_1_ready, meta={})
    assert get_check_streams(p) == 2
    set_check_streams_meta(p, 8)
    assert get_check_streams(p) == 8


def test_img_pr_live_streams_from_check_streams(monkeypatch) -> None:
    monkeypatch.setattr(app_settings.settings, "check_max_streams", 2)
    p = Project(slug="c", topic="t", status=ProjectStatus.enrich_1_ready, meta={})
    assert img_pr_live_streams(p) == 2
    set_check_streams_meta(p, 10)
    assert img_pr_live_streams(p) == 10
    set_check_streams_meta(p, 0)
    assert img_pr_live_streams(p) == 1
