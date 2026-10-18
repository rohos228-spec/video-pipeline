"""П.3: кандидат QC без якорь_n не затирает починенные шоты."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.montage_scene_improve import stage_qc


@pytest.mark.asyncio
async def test_stage_qc_copies_anchor_and_rejects_shorter_candidate() -> None:
    parent = SimpleNamespace(uuid="frame-qc-1", number=3)
    shots = [
        {
            "id": "3-S1-K1",
            "порядок": 1,
            "якорь_n": 1,
            "действие": "открывает дверь комнаты",
            "план": "НЕ_ИЗ_СПИСКА",
            "ракурс": "фронт",
            "движение": "статика",
            "стык": "cut",
            "закадр": "Он открывает дверь и входит в комнату.",
            "место": "комната",
        },
        {
            "id": "3-S1-K2",
            "порядок": 2,
            "якорь_n": 2,
            "действие": "смотрит в окно на двор",
            "план": "СРЕДНИЙ",
            "ракурс": "слева",
            "движение": "статика",
            "стык": "cut",
            "закадр": "Потом долго смотрит в окно на пустой двор.",
            "место": "комната",
        },
    ]
    units = [
        {"n": 1, "закадр": shots[0]["закадр"], "бюджет": 2},
        {"n": 2, "закадр": shots[1]["закадр"], "бюджет": 2},
    ]

    async def ask(_prompt: str) -> str:
        return (
            '{"ops":[{"frame_uuid":"frame-qc-1","fields":{"кадры":['
            '{"действие":"открывает дверь комнаты","план":"ОБЩИЙ","ракурс":"фронт",'
            '"движение":"статика","стык":"cut","закадр":"Он открывает дверь и входит в комнату."},'
            '{"действие":"смотрит в окно на двор","план":"СРЕДНИЙ","ракурс":"слева",'
            '"движение":"статика","стык":"cut","закадр":"Потом долго смотрит в окно на пустой двор."}'
            "]}}]}"
        )

    out, _soft = await stage_qc(
        ask,
        parent=parent,
        vo=shots[0]["закадр"] + " " + shots[1]["закадр"],
        shots=shots,
        units=units,
        place="комната",
        nodes=[],
    )
    assert [shot.get("якорь_n") for shot in out] == [1, 2]
    assert len(out) == 2
