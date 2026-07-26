"""Дефолтный агент-проверки по вышестоящей ноде + динамический формат."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.services.check_analysis import SCHEMA_ID, format_target_hint
from app.services.gpt_operator import (
    default_check_prompt_for_node,
    project_format_hint_for_check,
    upstream_node_type_for_check,
)


def _project_with_check(upstream_type: str, **fields) -> Project:
    meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_up", "type": upstream_type, "position": {"x": 0, "y": 0}},
                {"id": "n_check", "type": "excel_gpt", "position": {"x": 1, "y": 0}},
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "n_up",
                    "target": "n_check",
                    "data": {"kind": "after"},
                },
            ],
        },
        "excel_gpt_nodes": {"n_check": {"role": "review", "transport": "api"}},
    }
    return Project(
        slug="chk",
        topic="t",
        status=ProjectStatus.new,
        hero_mode="no_hero",
        meta=meta,
        **fields,
    )


def test_upstream_node_type_detected() -> None:
    p = _project_with_check("images")
    assert upstream_node_type_for_check(p, "n_check") == "images"


def test_default_prompt_matches_upstream_step() -> None:
    p = _project_with_check("images")
    prompt = default_check_prompt_for_node(p, "n_check")
    assert prompt is not None
    assert SCHEMA_ID in prompt
    assert "files_present" in prompt  # стабильный check-id агента images


def test_default_prompt_for_excel_upstream() -> None:
    p = _project_with_check("image_prompts")
    prompt = default_check_prompt_for_node(p, "n_check")
    assert prompt is not None
    assert "prompt_per_frame" in prompt


def test_default_prompt_none_without_upstream() -> None:
    p = Project(
        slug="chk2",
        topic="t",
        status=ProjectStatus.new,
        hero_mode="no_hero",
        meta={
            "canvas_graph": {"nodes": [{"id": "n_check", "type": "excel_gpt"}], "edges": []},
            "excel_gpt_nodes": {"n_check": {"role": "review"}},
        },
    )
    assert default_check_prompt_for_node(p, "n_check") is None


def test_format_hint_is_dynamic_not_static() -> None:
    # Формат берётся из полей проекта, а не зашитый 9:16.
    p = _project_with_check("images", aspect_ratio="1:1", image_resolution="1080x1080")
    hint = project_format_hint_for_check(p, "n_check")
    assert "1:1" in hint
    assert "1080x1080" in hint
    assert "9:16" not in hint


def test_format_hint_default_aspect_when_unset() -> None:
    p = _project_with_check("images")
    hint = project_format_hint_for_check(p, "n_check")
    assert "9:16" in hint  # дефолт, если поле проекта пустое


def test_format_hint_skipped_for_nonvisual_upstream() -> None:
    # Для текстовых нод (plan/script) формат-подсказка не нужна.
    p = _project_with_check("plan", aspect_ratio="1:1")
    assert project_format_hint_for_check(p, "n_check") == ""


def test_format_target_hint_pure() -> None:
    assert format_target_hint(None, None, None) == "Целевой формат проекта: соотношение сторон = 9:16."
    out = format_target_hint("4:5", "1024", "720p")
    assert "4:5" in out and "1024" in out and "720p" in out
