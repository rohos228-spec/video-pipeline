"""Fleet montage handoff: agent defers assemble to hub."""

from __future__ import annotations

from app.fleet.montage_handoff import should_defer_assemble_to_hub
from app.models import Project
from app.settings import settings


def test_defer_on_agent_with_send_to_main(monkeypatch) -> None:
    monkeypatch.setattr(settings, "fleet_enabled", True)
    monkeypatch.setattr(settings, "fleet_role", "agent")
    project = Project(
        slug="t",
        topic="t",
        meta={"node_step_params": {"assemble": {"send_to_main_pc": True}}},
    )
    assert should_defer_assemble_to_hub(project) is True


def test_no_defer_on_hub(monkeypatch) -> None:
    monkeypatch.setattr(settings, "fleet_enabled", True)
    monkeypatch.setattr(settings, "fleet_role", "hub")
    project = Project(
        slug="t",
        topic="t",
        meta={"node_step_params": {"assemble": {"send_to_main_pc": True}}},
    )
    assert should_defer_assemble_to_hub(project) is False


def test_no_defer_when_send_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "fleet_enabled", True)
    monkeypatch.setattr(settings, "fleet_role", "agent")
    project = Project(
        slug="t",
        topic="t",
        meta={"node_step_params": {"assemble": {"send_to_main_pc": False}}},
    )
    assert should_defer_assemble_to_hub(project) is False


def test_is_montage_deferred_when_flagged(monkeypatch) -> None:
    from app.fleet.montage_handoff import is_montage_deferred_to_hub

    monkeypatch.setattr(settings, "fleet_enabled", True)
    monkeypatch.setattr(settings, "fleet_role", "agent")
    project = Project(
        slug="t",
        topic="t",
        meta={
            "fleet_montage_deferred": True,
            "montage_ready": True,
            "node_step_params": {"assemble": {"send_to_main_pc": True}},
        },
    )
    assert is_montage_deferred_to_hub(project) is True


def test_not_deferred_when_send_disabled(monkeypatch) -> None:
    from app.fleet.montage_handoff import is_montage_deferred_to_hub

    monkeypatch.setattr(settings, "fleet_enabled", True)
    monkeypatch.setattr(settings, "fleet_role", "agent")
    project = Project(
        slug="t",
        topic="t",
        meta={
            "fleet_montage_deferred": True,
            "montage_ready": True,
            "node_step_params": {"assemble": {"send_to_main_pc": False}},
        },
    )
    assert is_montage_deferred_to_hub(project) is False
