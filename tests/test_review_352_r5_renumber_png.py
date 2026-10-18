"""Ревью #352: реальный дубль сохраняет png, служебные ключи не утекают."""

from app.services.scene_plan import _renumber_scene, apply_scene_plan_cells


def test_real_duplicate_keeps_png_and_tmp_keys_do_not_leak() -> None:
    shots = [
        {"id": "1-S1-K1", "png": "first.png"},
        {"id": "1-S1-K1", "png": "dup.png"},
    ]
    _renumber_scene(shots, "1-S1")
    assert shots[0]["id"] == "1-S1-K1"
    assert shots[0]["png"] == "first.png"
    assert shots[1]["id"] != "1-S1-K1"
    assert shots[1]["png"] == "dup.png"

    cell = [
        {
            "id": "1-S1-K1",
            "_inserted": True,
            "_cell": "leak",
            "закадр": "Он стоит у двери.",
        }
    ]
    apply_scene_plan_cells(
        [{"key": "a", "shots": cell, "plan": None, "owned": True}]
    )
    assert "_inserted" not in cell[0]
    assert "_cell" not in cell[0]
