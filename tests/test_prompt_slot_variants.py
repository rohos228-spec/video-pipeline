"""resolve_project_prompt_name учитывает meta.prompt_slot_variants (Node Studio)."""

from __future__ import annotations

from unittest.mock import patch

from app.services.prompt_library import resolve_project_prompt_name


def test_resolve_prefers_prompt_overrides_over_stale_meta_slot() -> None:
    """Активный override проекта важнее чужой ноды в meta."""
    meta = {
        "prompt_slot_variants": {
            "n_old": {"main": "default"},
            "n_enrich_1": {"main": "От клода"},
        }
    }
    overrides = {"enrich_1": "От клода"}
    with patch(
        "app.services.prompt_library.prompt_path",
        side_effect=lambda step, name: type("P", (), {"exists": lambda self: True})(),
    ):
        name = resolve_project_prompt_name(overrides, "enrich_1", meta=meta)
    assert name == "От клода"


def test_resolve_uses_meta_when_no_override() -> None:
    meta = {"prompt_slot_variants": {"n1": {"main": "custom_slot"}}}
    with patch(
        "app.services.prompt_library.prompt_path",
        side_effect=lambda step, name: type("P", (), {"exists": lambda self: True})(),
    ):
        name = resolve_project_prompt_name({}, "enrich_1", meta=meta)
    assert name == "custom_slot"


def test_resolve_falls_back_to_prompt_overrides() -> None:
    meta: dict = {}
    overrides = {"enrich_1": "custom_slot"}
    with patch(
        "app.services.prompt_library.prompt_path",
        side_effect=lambda step, name: type("P", (), {"exists": lambda self: True})(),
    ):
        name = resolve_project_prompt_name(overrides, "enrich_1", meta=meta)
    assert name == "custom_slot"
