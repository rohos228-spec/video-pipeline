"""QC-отчёт не считает неактивные шоты. Список кадры[] не режется."""

from __future__ import annotations

from types import SimpleNamespace


def test_shots_report_collect_and_plans_skip_inactive() -> None:
    """_collect_shots и _plans: неактивный шот не входит в отчёт и схему."""
    from app.services.shots_report import build_shots_report_model

    kadry = [
        {
            "id": "1-S1-K1",
            "порядок": 1,
            "сцена": 1,
            "план": "ОБЩИЙ",
            "действие": "живой шаг у стола",
            "закадр": "Живой закадр кадра целиком.",
            "зона": "стол",
            "камера": {"где": "юг", "смотрит": "север"},
        },
        {
            "id": "1-S1-K2",
            "порядок": 2,
            "сцена": 1,
            "план": "СРЕДНИЙ",
            "действие": "второй живой шаг",
            "закадр": "Второй живой кусок.",
            "зона": "стол",
            "камера": {"где": "север", "смотрит": "юг"},
        },
        {
            "id": "1-S1-K9",
            "порядок": 9,
            "сцена": 1,
            "план": "КРУПНЫЙ",
            "действие": "МЁРТВЫЙ ШАГ",
            "закадр": "Чужой закадр.",
            "зона": "стол",
            "камера": {"где": "запад", "смотрит": "восток"},
            "удалён": True,
        },
    ]
    fr = SimpleNamespace(
        number=1,
        uuid="e" * 24,
        voiceover_text="Живой закадр кадра целиком. Второй живой кусок.",
        image_prompt="",
        animation_prompt="",
        sort_key=1.0,
        attrs={
            "кадры": kadry,
            "площадка": {"зоны": [{"id": "стол", "предметы": []}]},
            "camera_subdivide": {"shot_id": "1-S1-K1", "сцена": 1, "role": "vo_parent"},
        },
    )
    model = build_shots_report_model([fr])
    ids = [s["id"] for s in model["shots"]]
    assert "1-S1-K9" not in ids
    assert "1-S1-K1" in ids and "1-S1-K2" in ids
    assert model["shot_count"] == 2
    assert "МЁРТВЫЙ" not in str(model["plans"])
    assert "К9" not in str(model["plans"])
    assert len(fr.attrs["кадры"]) == 3
    assert fr.attrs["кадры"][2]["удалён"] is True
