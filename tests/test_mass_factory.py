"""Тесты фабрики видео (очередь, clone meta)."""

from __future__ import annotations

from app.models import Project
from app.services.mass_factory import (
    build_child_meta,
    is_mass_factory_parent,
    queue_state,
)


def test_build_child_meta_strips_queue_keys() -> None:
    parent_meta = {
        "mass_factory": True,
        "mass_queue_topics": ["a"],
        "ai_control": True,
        "auto_review_kinds": ["approve_plan"],
        "prompt_slot_variants": {"plan": {"main": "default"}},
    }
    child = build_child_meta(parent_meta, parent_id=1, lane_position=2)
    assert child["mass_parent_id"] == 1
    assert child["mass_lane_position"] == 2
    assert child["ai_control"] is True
    assert "mass_queue_topics" not in child
    assert "mass_factory" not in child


def test_queue_state_reads_parent_meta() -> None:
    p = Project(slug="f", topic="Фабрика", hero_mode="auto")
    p.meta = {
        "mass_factory": True,
        "mass_queue_active": True,
        "mass_queue_topics": ["a", "b"],
        "mass_queue_cursor": 1,
    }
    qs = queue_state(p)
    assert qs["active"] is True
    assert qs["topics"] == ["a", "b"]
    assert qs["cursor"] == 1
    assert is_mass_factory_parent(p)
