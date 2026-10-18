"""Повторное ревью #352 №7: _drop_extra_shots останавливается на required=1.

Совпадает с #354 bbdd47f2.
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


def test_drop_extra_stops_at_required_and_keeps_ids(warn_log) -> None:
    from app.services.shot_templates import _drop_extra_shots

    rows = [
        {"shot_id": "T1-K1", "required": 0},
        {"shot_id": "T1-K2", "required": 1},
        {"shot_id": "T1-K3", "required": 1},
        {"shot_id": "T1-K4", "required": 0},
    ]
    shots = [{"id": f"s{i}", "шаблон": "T1", "закадр": ""} for i in range(4)]
    out = _drop_extra_shots(shots, 1, rows)
    assert [s["id"] for s in out] == ["s0", "s1", "s2", "s3"]
    assert out[1].get("карантин") is not True
    assert out[2].get("карантин") is not True
    assert out[3]["карантин"] is True
    assert any("required=1" in line for line in warn_log)
