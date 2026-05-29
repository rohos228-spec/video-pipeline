"""node_step_params: блок параметров в GPT-сообщении."""

from __future__ import annotations

from app.models import Project
from app.services import gpt_text_builder as gtb
from app.services.node_step_params import (
    append_step_params_to_gpt_text,
    build_step_params_block,
    duration_seconds_for_step,
)


def test_script_inherits_plan_duration() -> None:
    p = Project(topic="t")
    p.meta = {
        "node_step_params": {
            "plan": {"duration_seconds": 60},
            "script": {},
        }
    }
    assert duration_seconds_for_step(p, "script") == 60


def test_plan_params_block_with_blanks() -> None:
    p = Project(topic="t")
    p.meta = {"node_step_params": {"plan": {}}}
    block = build_step_params_block(p, "plan")
    assert "Длина ____ секунд" in block
    assert "× 14) = ____" in block


def test_plan_params_block_with_values() -> None:
    p = Project(topic="t")
    p.meta = {"node_step_params": {"plan": {"duration_seconds": 65}}}
    block = build_step_params_block(p, "plan")
    assert "Длина 65 секунд" in block
    assert "= 910" in block


def test_split_params_block() -> None:
    p = Project(topic="t")
    p.meta = {
        "node_step_params": {
            "split": {
                "cell_min_chars": 40,
                "cell_max_chars": 110,
                "cell_avg_min": 55,
                "cell_avg_max": 90,
            }
        }
    }
    block = build_step_params_block(p, "split")
    assert "Минимальное количество символов в ячейке 40" in block
    assert "от 55 до 90" in block


def test_get_effective_text_appends_params() -> None:
    p = Project(topic="test topic")
    p.meta = {"node_step_params": {"plan": {"duration_seconds": 60}}}
    text = gtb.get_effective_text(p, "plan")
    assert "Тема ролика" in text
    assert "---" in text
    assert "Общий план" in text
    assert "840" in text


def test_append_preserves_override_body() -> None:
    p = Project(topic="t")
    p.gpt_text_overrides = {"plan": "Мой текст"}
    p.meta = {"node_step_params": {"plan": {"duration_seconds": 50}}}
    text = append_step_params_to_gpt_text(p, "plan", "Мой текст")
    assert text.startswith("Мой текст")
    assert "700" in text
