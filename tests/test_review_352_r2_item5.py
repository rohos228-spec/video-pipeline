"""Повторное ревью #352 №5: QC coalesce правит по id, список не подменяет.

Совпадает с #354 cbc49690 (база bbdd47f2).
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


def test_qc_coalesce_marks_omitted_shots_and_keeps_empty_vo(warn_log) -> None:
    from app.services.apply_ops_batches import coalesce_cell_shot_ops

    uid = "abc"
    frames = [
        {
            "uuid": uid,
            "кадры": [
                {"id": "1-S1-K1", "закадр": "раз", "действие": "старое"},
                {"id": "1-S1-K2", "закадр": "", "действие": "пауза"},
                {"id": "1-S1-K3", "закадр": "три", "действие": "хвост"},
            ],
        }
    ]
    merged = coalesce_cell_shot_ops(
        [
            {
                "frame_uuid": uid,
                "fields": {
                    "кадры": [{"id": "1-S1-K1", "закадр": "раз", "действие": "новое"}]
                },
            }
        ],
        frames,
        patch=True,
    )
    rows = merged[0]["fields"]["кадры"]
    assert [s["id"] for s in rows] == ["1-S1-K1", "1-S1-K2", "1-S1-K3"]
    assert rows[0]["действие"] == "новое"
    assert rows[0].get("карантин") is not True
    assert rows[1]["карантин"] is True
    assert "QC-список короче" in rows[1]["пометка"]
    assert rows[2]["карантин"] is True
    assert any("WARN coalesce_cell_shot_ops" in line for line in warn_log)

    fresh = coalesce_cell_shot_ops(
        [
            {
                "frame_uuid": uid,
                "fields": {
                    "кадры": [
                        {"id": "1-S1-K1", "закадр": "", "действие": "пауза QC"},
                        {"id": "1-S1-K2", "закадр": "", "действие": "пауза"},
                        {"id": "1-S1-K3", "закадр": "три", "действие": "хвост"},
                    ]
                },
            }
        ],
        frames,
        patch=True,
    )[0]["fields"]["кадры"]
    assert fresh[0]["действие"] == "пауза QC"
    assert fresh[0].get("карантин") is not True
    assert fresh[0]["закадр"] == ""
