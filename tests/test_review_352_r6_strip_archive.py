"""Ревью #352 п.3: strip_non_scene_kadry архивирует скрытые строки и shots."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.vo_shot_expand import strip_non_scene_kadry


def test_strip_archives_hidden_row_and_shots() -> None:
    stub = SimpleNamespace(
        attrs={
            "кадры": [
                {"id": "B01-K1", "действие": "ищут"},
                {"id": "B01-K9", "закадр": "скрытая строка", "активен": False},
            ],
            "shots": [{"id": "S-1", "закадр": "строка shots"}],
        }
    )
    assert strip_non_scene_kadry(stub) is True
    archived = stub.attrs.get("кадры_archived") or []
    ids = [row.get("id") for row in archived]
    assert "B01-K1" in ids
    assert "B01-K9" in ids
    assert "S-1" in ids
    hidden = next(row for row in archived if row.get("id") == "B01-K9")
    assert hidden["закадр"] == "скрытая строка"
    assert stub.attrs.get("кадры") == []
    assert stub.attrs.get("shots") == []
