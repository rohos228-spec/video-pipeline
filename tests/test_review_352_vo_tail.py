"""П.6: хвост лестницы длиннее числа кадров клеится к последнему."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.vo_shot_expand import apply_shot_voiceover_to_cells


def _pair(full: str, kadry: list[dict], pu: str = "hh" * 12):
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=full,
        attrs={
            "кадры": [dict(row) for row in kadry],
            "vo_cell_full": full,
            "camera_subdivide": {"role": "vo_parent", "parent_uuid": pu},
        },
    )
    child = SimpleNamespace(
        uuid="ii" * 12,
        voiceover_text="",
        attrs={"camera_subdivide": {"role": "shot", "parent_uuid": pu}},
    )
    return parent, child


def test_voiceover_tail_glues_to_last_child() -> None:
    full = "Первая фраза целиком. Вторая тоже на месте. Третья остаётся в лестнице."
    parent, child = _pair(
        full,
        [
            {"id": "1-K1", "закадр": "Первая фраза целиком."},
            {"id": "1-K2", "закадр": "Вторая тоже на месте."},
            {"id": "1-K3", "закадр": "Третья остаётся в лестнице."},
        ],
    )
    apply_shot_voiceover_to_cells([parent, child])
    assert parent.voiceover_text == "Первая фраза целиком."
    assert "Вторая тоже на месте." in child.voiceover_text
    assert "Третья остаётся в лестнице." in child.voiceover_text
    pieces = [row["закадр"] for row in parent.attrs["кадры"]]
    assert pieces[2] == "Третья остаётся в лестнице."


def test_voiceover_extra_member_stays_empty() -> None:
    full = "Одна целая фраза без хвоста на второй кадр."
    parent, child = _pair(full, [{"id": "1-K1", "закадр": full}], pu="jj" * 12)
    apply_shot_voiceover_to_cells([parent, child])
    assert parent.voiceover_text == full
    assert child.voiceover_text == ""
