"""Тесты app.services.disabled_nodes."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.services.disabled_nodes import (
    disabled_node_types,
    is_step_disabled,
    node_type_from_key,
    skip_disabled_running,
)


def test_node_type_from_key_default_and_timestamp() -> None:
    assert node_type_from_key("n_plan") == "plan"
    assert node_type_from_key("n_plan_1700000000") == "plan"
    assert node_type_from_key("n_enrich_1") == "enrich_1"
    assert node_type_from_key("n_image_prompts_99") == "image_prompts"


def test_disabled_node_types_from_meta() -> None:
    p = Project(topic="t", slug="t", meta={"disabled_nodes": ["n_hero", "n_enrich_2"]})
    types = disabled_node_types(p)
    assert types == {"hero", "enrich_2"}


def test_is_step_disabled() -> None:
    p = Project(topic="t", slug="t", meta={"disabled_nodes": ["n_script"]})
    assert is_step_disabled(p, "script") is True
    assert is_step_disabled(p, "plan") is False


def test_skip_disabled_running_sync_returns_target() -> None:
    p = Project(
        topic="t",
        slug="t",
        meta={"disabled_nodes": ["n_script", "n_split"]},
    )
    nxt = skip_disabled_running(p, ProjectStatus.scripting)
    assert nxt == ProjectStatus.scripting
