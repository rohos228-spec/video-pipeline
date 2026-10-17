"""Повторное ревью #352 №6: пустая оболочка помечается, не выкидывается.

Кадр с заполненной «реплика» или «говорит» остаётся активным без пометки.
"""

from __future__ import annotations

import pytest
from loguru import logger


@pytest.fixture
def warn_log():
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


def test_repair_shot_vo_marks_empty_shell_and_keeps_it(warn_log) -> None:
    from app.services.scene_shot_grammar import repair_shot_vo_lengths

    vo = "Следователь открыл толстую папку на столе."
    shots = [
        {"id": "real", "действие": "открывает папку", "закадр": vo},
        {"id": "shell"},
        {"id": "reply-only", "реплика": "алло"},
    ]
    repair_shot_vo_lengths(shots, vo)
    assert [s["id"] for s in shots] == ["real", "shell", "reply-only"]
    assert shots[1]["карантин"] is True
    assert "пустая оболочка" in shots[1]["пометка"]
    assert shots[2].get("карантин") is not True
    assert "пометка" not in shots[2]
    assert shots[2]["реплика"] == "алло"
    assert any("WARN repair_shot_vo_lengths" in line for line in warn_log)


def test_reply_only_shot_stays_active_without_mark() -> None:
    from app.services.scene_shot_grammar import repair_shot_vo_lengths

    shots = [
        {"id": "reply-only", "реплика": "Где вы были в десять?"},
        {"id": "speaker-only", "говорит": "Следователь"},
    ]
    repair_shot_vo_lengths(shots, "")
    assert [s["id"] for s in shots] == ["reply-only", "speaker-only"]
    assert shots[0]["реплика"] == "Где вы были в десять?"
    assert shots[0].get("карантин") is not True
    assert "пометка" not in shots[0]
    assert shots[1].get("карантин") is not True
    assert "пометка" not in shots[1]
