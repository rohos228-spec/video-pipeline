"""enrich_xlsx must not overwrite generating_videos / later media statuses."""

from __future__ import annotations

from types import SimpleNamespace

from app.models import ProjectStatus
from app.orchestrator.steps.enrich_xlsx import _apply_enrich_ready_status


def _proj(status: ProjectStatus) -> SimpleNamespace:
    return SimpleNamespace(id=56, status=status)


def test_enrich_ready_does_not_stomp_generating_videos() -> None:
    p = _proj(ProjectStatus.generating_videos)
    changed = _apply_enrich_ready_status(
        p,  # type: ignore[arg-type]
        running_status=ProjectStatus.enriching_5,
        ready_status=ProjectStatus.enrich_5_ready,
    )
    assert changed is False
    assert p.status is ProjectStatus.generating_videos


def test_enrich_ready_applies_from_running() -> None:
    p = _proj(ProjectStatus.enriching_5)
    changed = _apply_enrich_ready_status(
        p,  # type: ignore[arg-type]
        running_status=ProjectStatus.enriching_5,
        ready_status=ProjectStatus.enrich_5_ready,
    )
    assert changed is True
    assert p.status is ProjectStatus.enrich_5_ready


def test_enrich_ready_does_not_rollback_animation_prompts_ready() -> None:
    p = _proj(ProjectStatus.animation_prompts_ready)
    changed = _apply_enrich_ready_status(
        p,  # type: ignore[arg-type]
        running_status=ProjectStatus.enriching_5,
        ready_status=ProjectStatus.enrich_5_ready,
    )
    assert changed is False
    assert p.status is ProjectStatus.animation_prompts_ready
