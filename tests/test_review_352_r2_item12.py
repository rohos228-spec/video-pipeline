"""Повторное ревью #352 №12: генерация не затирает блоки оператора.

Совпадает с #354 bbdd47f2 и cbc49690 (хвостовые блоки без карантина).
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


def test_apply_generated_scenario_keeps_operator_blocks(warn_log) -> None:
    from app.services.nii67_card import apply_generated_scenario, default_card, insert_block

    card = insert_block(default_card(), "door1", 6, "custom")
    door = card["doors"][0]
    world = next(b for b in door["blocks"] if b["kind"] == "world_frame")
    world["text"] = "текст оператора"
    world["image_prompt"] = "промт оператора"
    world["image_name"] = "slot.png"
    before_ids = [b["id"] for b in door["blocks"]]
    scenario = {
        "theme": "Мокрый цех",
        "law": "Капли падают сами",
        "illustration": "герой выходит",
        "door_design": "стекло",
        "door_anim": "пар",
        "beats": [
            {
                "id": "entry",
                "text": "сгенерированный вход",
                "frames": [{"start_frame": "новый промт кадра"}],
            },
            {"id": "hook", "text": "сгенерированный выход"},
        ],
    }
    out = apply_generated_scenario(card, {"door1": scenario}, {"standard": "финал"})
    blocks = out["doors"][0]["blocks"]
    after_ids = [b["id"] for b in blocks]
    assert before_ids == [i for i in after_ids if i in before_ids]
    saved = next(b for b in blocks if b["id"] == world["id"])
    assert saved["text"] == "текст оператора"
    assert saved["image_prompt"] == "промт оператора"
    assert saved["image_name"] == "slot.png"
    assert saved.get("пометка")
    custom = next(b for b in blocks if b["kind"] == "custom")
    assert custom["id"] in before_ids
    assert custom.get("пометка")
    assert custom.get("карантин") is not True
    assert any("WARN apply_generated_scenario" in line for line in warn_log)
