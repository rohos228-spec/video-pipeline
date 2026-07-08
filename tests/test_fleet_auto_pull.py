"""Auto-pull: только fleet_send_requested (кнопка «Отправить»)."""

from __future__ import annotations

from app.fleet.pull_loop import _agent_project_eligible_for_pull


def test_eligible_only_after_send_requested() -> None:
    assert _agent_project_eligible_for_pull(
        {
            "montage_ready": True,
            "fleet_send_requested": True,
            "fleet_handoff_complete": False,
        }
    )
    assert not _agent_project_eligible_for_pull(
        {
            "montage_ready": True,
            "montage_handoff_pending": True,
            "fleet_send_requested": False,
        }
    )
    assert not _agent_project_eligible_for_pull(
        {
            "montage_ready": True,
            "fleet_send_requested": True,
            "fleet_handoff_complete": True,
        }
    )


def test_completed_handoff_not_deferred() -> None:
    from app.fleet.montage_handoff import is_montage_deferred_to_hub
    from app.models import Project, ProjectStatus

    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.music_ready,
        meta={
            "fleet_montage_deferred": True,
            "montage_ready": True,
            "fleet_handoff_complete": True,
        },
    )
    assert is_montage_deferred_to_hub(p) is False


def test_aborted_not_deferred() -> None:
    from app.fleet.montage_handoff import is_montage_handoff_pending
    from app.models import Project, ProjectStatus

    p = Project(
        slug="t",
        topic="t",
        status=ProjectStatus.music_ready,
        meta={
            "fleet_montage_deferred": True,
            "montage_ready": True,
            "fleet_transfer_aborted": True,
        },
    )
    assert is_montage_handoff_pending(p) is False
