"""П.5–6: скрытый шот без слота, хвост закадра клеится к последнему живому."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.vo_shot_expand import (
    active_planned_shots,
    apply_shot_voiceover_to_cells,
)


def test_hidden_shot_is_not_a_voiceover_slot() -> None:
    pu = "ee" * 12
    full = "Первая фраза целиком. Вторая тоже на месте. Третья остаётся в лестнице."
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=full,
        attrs={
            "кадры": [
                {"id": "1-K1", "закадр": "Первая фраза целиком."},
                {"id": "1-K2", "закадр": "Вторая тоже на месте.", "активен": False},
                {"id": "1-K3", "закадр": "Третья остаётся в лестнице."},
            ],
            "vo_cell_full": full,
            "camera_subdivide": {"role": "vo_parent", "parent_uuid": pu},
        },
    )
    child = SimpleNamespace(
        uuid="ff" * 12,
        voiceover_text="",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": pu}},
    )
    hidden = SimpleNamespace(
        uuid="gg" * 12,
        voiceover_text="",
        attrs={
            "активен": False,
            "удалён": True,
            "camera_subdivide": {"role": "shot", "parent_uuid": pu},
        },
    )
    assert [row["id"] for row in active_planned_shots(parent)] == ["1-K1", "1-K3"]
    apply_shot_voiceover_to_cells([parent, child, hidden])
    ids = [row["id"] for row in parent.attrs["кадры"]]
    assert ids == ["1-K1", "1-K2", "1-K3"]
    assert hidden.voiceover_text == ""
    blob = f"{parent.voiceover_text} {child.voiceover_text}"
    assert "Первая" in blob and "Третья" in blob
    assert "Вторая" not in blob
    hidden_shot = next(row for row in parent.attrs["кадры"] if row["id"] == "1-K2")
    assert "Вторая" in hidden_shot["закадр"]
