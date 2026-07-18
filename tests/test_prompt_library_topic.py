"""get_project_prompt: подстановка темы без NameError."""

from __future__ import annotations

from unittest.mock import patch

from app.models import Project
from app.services.prompt_library import get_project_prompt


def test_get_project_prompt_injects_topic(tmp_path) -> None:
    project = Project(slug="t", topic="Сталин", hero_mode="auto", prompt_overrides={"plan": "default"})
    master = "ТЕМА: [ВСТАВЬ ТЕМУ]"
    with patch("app.services.prompt_library.read_prompt", return_value=master):
        out = get_project_prompt(project, "plan")
    assert out == "ТЕМА: (Сталин)"
    assert "[ВСТАВЬ ТЕМУ]" not in out


def test_get_project_prompt_blocks_v2_no_nameerror() -> None:
    """blocks v2 path раньше падал: NameError actual_topic is not defined."""
    project = Project(
        slug="t2",
        topic="Дота",
        hero_mode="auto",
        prompt_overrides={"use_blocks_v2": True},
    )
    with (
        patch(
            "app.services.prompt_composer.compose_step",
            return_value="Сценарий про [ВСТАВЬ ТЕМУ]",
        ),
        patch(
            "app.services.prompt_composer.merge_project_prompt_config",
            return_value=({}, {}),
        ),
        patch("app.services.prompt_composer.project_uses_blocks_v2", return_value=True),
        patch(
            "app.services.prompt_composer.STEP_CODE_TO_COMPOSE",
            {"script": "02_script"},
        ),
    ):
        out = get_project_prompt(project, "script")
    assert "Дота" in out
    assert "[ВСТАВЬ ТЕМУ]" not in out
