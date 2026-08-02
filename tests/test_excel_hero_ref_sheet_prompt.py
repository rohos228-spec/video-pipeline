"""Реф-вариация персонажа должна собирать turnaround sheet, не scene."""

from __future__ import annotations

from app.services.excel_characters import (
    ExcelCharacter,
    build_ref_variation_sheet_prompt,
    is_polluted_character_field,
)


def test_polluted_character_name_detected() -> None:
    assert is_polluted_character_field("оставь формат неизменный")
    assert not is_polluted_character_field("Фридрих Ницше")


def test_ref_variation_prompt_forces_sheet_not_scene() -> None:
    ch = ExcelCharacter(
        id="c02",
        name="Ницше больной",
        look="осунувшееся лицо",
        clothes="больничная рубаха",
        char="безучастный",
        ref_ids=["c01"],
    )
    text = build_ref_variation_sheet_prompt(
        ch, style="archival noir watercolor grunge"
    )
    low = text.lower()
    assert "turnaround sheet" in low or "model sheet" in low
    assert "white background" in low
    assert "not a cinematic" in low or "not a story scene" in low
    assert "c02" in text
    assert "больничная рубаха" in text
    assert "watercolor" in low
    assert len(text) <= 4900
