"""Подстановка темы ролика в плейсхолдеры мастер-промтов."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.models import Project
from app.services import chatgpt_xlsx as cx
from app.services import gpt_text_builder as gtb


@pytest.mark.parametrize(
    ("template", "topic", "expected"),
    [
        ("ТЕМА: [ВСТАВЬ ТЕМУ]", "Сталин", "ТЕМА: (Сталин)"),
        ("Тема (тема ролика) для ролика", "Наука", "Тема (Наука) для ролика"),
        ("Topic: {{TOPIC}}", "Космос", "Topic: Космос"),
        ("{{VAR:PROJECT_TOPIC}}", "История", "История"),
        ("[вставь тему]", "Lower", "(Lower)"),
        ("", "ignored", ""),
        ("[ВСТАВЬ ТЕМУ]", "", "[ВСТАВЬ ТЕМУ]"),
    ],
)
def test_inject_topic_placeholders(template: str, topic: str, expected: str) -> None:
    assert gtb.inject_topic_placeholders(template, topic) == expected


def test_write_plan_prompt_file_replaces_topic_placeholder(tmp_path: Path) -> None:
    project = Project(slug="plan-topic", topic="Жизнь Сталина", hero_mode="auto")
    project.id = 1
    master = "ТЕМА РОЛИКА:\n[ВСТАВЬ ТЕМУ]\n\nДальше инструкции."

    with patch("app.services.chatgpt_xlsx.get_project_prompt", return_value=master):
        prompt_file = cx.write_plan_prompt_file(project, tmp_path, ts="t")

    content = prompt_file.read_text(encoding="utf-8")
    assert "[ВСТАВЬ ТЕМУ]" not in content
    assert "(Жизнь Сталина)" in content
    assert content.startswith("Тема ролика: (Жизнь Сталина)")
