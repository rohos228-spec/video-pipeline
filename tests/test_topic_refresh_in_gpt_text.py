"""Актуальная тема ролика в GPT-тексте (не stale override с родителя)."""

from __future__ import annotations

from app.models import Project, ProjectStatus
from app.services.gpt_text_builder import (
    get_effective_text,
    refresh_topic_line_in_text,
)


def test_refresh_topic_line_paren() -> None:
    old = "Тема ролика: (Старое имя abbadon).\n\nПрикреплены 2 файла:"
    out = refresh_topic_line_in_text(old, "Phantom Assassin")
    assert "Phantom Assassin" in out
    assert "abbadon" not in out


def test_refresh_topic_line_guillemets() -> None:
    old = "Тема ролика: «Старое».\nДалее текст"
    out = refresh_topic_line_in_text(old, "Новая тема")
    assert out.startswith("Тема ролика: «Новая тема».")


def test_get_effective_text_refreshes_override_topic() -> None:
    p = Project(
        slug="x",
        topic="Актуальная тема bristleback",
        status=ProjectStatus.new,
        gpt_text_overrides={
            "plan": (
                "Тема ролика: (История abbadon сделай сценарий).\n\n"
                "Прикреплены 2 файла:\n1) prompt_plan.md\n2) project.xlsx"
            )
        },
    )
    text = get_effective_text(p, "plan")
    assert "Актуальная тема bristleback" in text
    assert "abbadon" not in text
