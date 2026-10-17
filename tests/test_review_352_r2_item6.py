"""Повторное ревью #352 №6: пустая оболочка помечается, не выкидывается.

Совпадает с #354 bbdd47f2. Кадр только с «реплика»/«говорит» по этому
предикату всё ещё пустая оболочка: в хунке нет этих полей.
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
    assert shots[2]["карантин"] is True
    assert any("WARN repair_shot_vo_lengths" in line for line in warn_log)
