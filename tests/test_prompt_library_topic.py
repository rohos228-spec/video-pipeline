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
