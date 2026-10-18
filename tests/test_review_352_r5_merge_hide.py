"""Ревью #352: слияние кадров и реестра не стирает скрытие и строки без id."""

from types import SimpleNamespace

from app.services.db_apply import merge_kadry_keep_missing, upsert_scene_registry


def test_merge_keeps_hidden_marks_and_row_without_id() -> None:
    out = merge_kadry_keep_missing(
        [
            {
                "id": "K2",
                "активен": False,
                "план": "ОБЩИЙ",
                "удалён": True,
                "карантин": True,
                "пометка": "скрыт пользователем",
            },
            {"план": "без id", "закадр": "хвост без идентификатора"},
        ],
        [{"id": "K2", "план": "КРУПНЫЙ", "активен": True}],
    )
    k2 = next(row for row in out if row.get("id") == "K2")
    assert k2["активен"] is False
    assert k2["план"] == "КРУПНЫЙ"
    assert k2["удалён"] is True
    assert k2["карантин"] is True
    assert k2["пометка"] == "скрыт пользователем"
    assert any(
        not row.get("id") and row.get("закадр") == "хвост без идентификатора"
        for row in out
    )

    project = SimpleNamespace(
        meta={
            "scene_registry": [
                {
                    "id_scene": "scene_02",
                    "start_words": "вечер",
                    "end_words": "ночь",
                    "активен": False,
                    "пометка": "скрытая сцена",
                },
                {"место": "карточка без id"},
            ]
        }
    )
    upsert_scene_registry(
        project,
        [
            {
                "id_scene": "scene_02",
                "start_words": "вечер",
                "end_words": "полночь",
                "активен": True,
            }
        ],
    )
    registry = project.meta["scene_registry"]
    scene = next(row for row in registry if row.get("id_scene") == "scene_02")
    assert scene["активен"] is False
    assert scene["end_words"] == "полночь"
    assert scene["пометка"] == "скрытая сцена"
    assert any(row.get("место") == "карточка без id" for row in registry)
