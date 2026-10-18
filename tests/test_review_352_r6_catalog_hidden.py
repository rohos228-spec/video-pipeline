"""Ревью #352 п.4: каталог не оживляет шот, скрытый merge."""

from __future__ import annotations

from app.services.shot_templates import clear_shot_templates_cache, fill_kadry_from_catalog


def test_fill_catalog_keeps_merge_hidden_shot() -> None:
    clear_shot_templates_cache()
    out = fill_kadry_from_catalog(
        [
            {
                "id": "1-S1-K2",
                "сцена": 1,
                "шаблон": "T6",
                "активен": False,
                "пометка": "merge",
                "закадр": "Он выходит со двора",
                "действие": "Герой выходит со двора и смотрит на дорогу",
            }
        ],
        "1. двор — герой выходит и идёт к воротам\n"
        "(Он выходит со двора и идёт к воротам.)\n",
        cell_number=1,
    )
    row = next(item for item in out if item.get("id") == "1-S1-K2")
    assert row.get("активен") is False
    assert row.get("пометка") == "merge"
