"""Ревью #352 п.1: закадр скрытого шота не выпадает из озвучки живых кадров."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.vo_shot_expand import apply_shot_voiceover_to_cells


def test_live_voiceover_join_keeps_full_and_hidden_shot_text() -> None:
    full = "Альфа стоит у двери и ждёт. Хвост кадра остаётся в ячейке целиком."
    hidden_piece = "Хвост кадра остаётся в ячейке целиком."
    pu = "ee" * 12
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=full,
        attrs={
            "vo_cell_full": full,
            "camera_subdivide": {"role": "vo_parent", "parent_uuid": pu},
            "кадры": [
                {"id": "3-K1", "закадр": "Альфа стоит у двери и ждёт."},
                {
                    "id": "3-K9",
                    "закадр": hidden_piece,
                    "активен": False,
                },
            ],
        },
    )
    apply_shot_voiceover_to_cells([parent])
    stored = parent.attrs["кадры"]
    by_id = {row["id"]: row for row in stored}
    live_join = " ".join(
        piece
        for piece in [parent.voiceover_text or ""]
        if str(piece).strip()
    )
    assert live_join == full
    assert by_id["3-K9"]["закадр"] == hidden_piece
    assert by_id["3-K9"]["активен"] is False
