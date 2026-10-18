"""П.5: normalize_card не выбрасывает блоки и не теряет длинный текст."""

from __future__ import annotations

from app.services.nii67_card import MAX_BLOCKS, TEXT_LIMIT, normalize_card


def test_normalize_card_keeps_overflow_blocks_and_long_text() -> None:
    blocks = [
        {"id": f"b{i}", "kind": "custom", "text": f"блок {i}"}
        for i in range(MAX_BLOCKS + 3)
    ]
    blocks[0]["text"] = "я" * (TEXT_LIMIT + 25)
    card = normalize_card({"doors": [{"id": "door1", "blocks": blocks}]})
    door = card["doors"][0]
    assert len(door["blocks"]) == MAX_BLOCKS + 3
    assert door["blocks"][0]["text"] == "я" * (TEXT_LIMIT + 25)
    archive = card.get("text_archive") or door.get("text_archive") or []
    assert archive
