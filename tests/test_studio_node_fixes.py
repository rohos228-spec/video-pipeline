"""Unit tests for Studio node runners, hero skip logic, and plan extraction."""

from __future__ import annotations

import pytest
from app.services.xlsx_step_runners import extract_general_plan_from_gpt_reply


def test_extract_general_plan_from_plain_markdown_reply() -> None:
    """Ensure plan extraction succeeds even if LLM returns markdown sections without TSV blocks."""
    reply = """
## Общий план
1. Введение: Футуристический город 2050 года
2. Развитие: Утренние технологии и ИИ-помощники
3. Кульминация: Полёт на аэротакси
4. Заключение: Вечер в умном доме
"""
    plan = extract_general_plan_from_gpt_reply(reply)
    assert bool(plan)
    assert "Футуристический город 2050 года" in plan
    assert len(plan) > 50


def test_extract_general_plan_from_tsv_block() -> None:
    """Ensure plan extraction extracts clean TSV lines from code blocks."""
    reply = """
Here is the plan:
```tsv
# Лист: план
Общая идея	День в 2050 году
Поэпизодник	1. Утро в умном доме
```
"""
    plan = extract_general_plan_from_gpt_reply(reply)
    assert bool(plan)
    assert "День в 2050 году" in plan


def test_extract_general_plan_rejects_empty_garbage() -> None:
    """Ensure empty or too short replies return empty string."""
    assert extract_general_plan_from_gpt_reply("") == ""
    assert extract_general_plan_from_gpt_reply("   ") == ""
    assert extract_general_plan_from_gpt_reply("ok") == ""