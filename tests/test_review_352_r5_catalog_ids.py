"""Ревью #352: каталог не подменяет исходный id кадра с медиа."""

from app.services.shot_templates import clear_shot_templates_cache, fill_kadry_from_catalog


def test_catalog_keeps_source_ids_with_media() -> None:
    clear_shot_templates_cache()
    out = fill_kadry_from_catalog(
        [
            {
                "id": "1-S1-K2",
                "сцена": 1,
                "шаблон": "T6",
                "png": "k2.png",
                "закадр": "Он выходит со двора",
                "действие": "Герой выходит со двора и смотрит на дорогу",
            },
            {
                "id": "1-S1-K5",
                "сцена": 1,
                "шаблон": "T6",
                "png": "k5.png",
                "закадр": "и идёт к воротам.",
                "действие": "Герой идёт по дороге к новым воротам",
            },
        ],
        "1. двор — герой выходит и идёт к воротам\n"
        "(Он выходит со двора и идёт к воротам.)\n",
        cell_number=1,
    )
    by_id = {str(row.get("id")): row for row in out}
    assert "1-S1-K2" in by_id
    assert "1-S1-K5" in by_id
    assert by_id["1-S1-K2"].get("png") == "k2.png"
    assert by_id["1-S1-K5"].get("png") == "k5.png"
