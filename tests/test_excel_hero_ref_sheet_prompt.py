"""Реф-вариация персонажа должна собирать turnaround sheet, не scene."""

from __future__ import annotations

from app.services.excel_characters import (
    ExcelCharacter,
    build_ref_variation_sheet_prompt,
    character_blocks_hero,
    is_polluted_character_field,
)


def test_polluted_character_name_detected() -> None:
    assert is_polluted_character_field("оставь формат неизменный")
    assert not is_polluted_character_field("Фридрих Ницше")


def test_variation_marker_name_ok_when_parent_in_rules() -> None:
    """Агент так помечает реф-вариацию: имя-маркер + правила = c01."""
    variation = ExcelCharacter(
        id="c07",
        name="оставь формат неизменный",
        look="осанка скованная",
        clothes="тёмное платье без украшений",
        rules="c01",
        ref_ids=["c01"],
    )
    standalone = ExcelCharacter(
        id="c09",
        name="оставь формат неизменный",
        look="мужчина",
        ref_ids=[],
    )
    assert character_blocks_hero(variation) is False
    assert character_blocks_hero(standalone) is True


def test_ref_variation_prompt_omits_format_marker_name() -> None:
    ch = ExcelCharacter(
        id="c07",
        name="оставь формат неизменный",
        look="осанка скованная",
        clothes="тёмное платье",
        char="унижена",
        ref_ids=["c01"],
    )
    text = build_ref_variation_sheet_prompt(ch, style="")
    assert "оставь формат" not in text.lower()
    assert "неизменн" not in text.lower()
    assert "осанка скованная" in text or "Appearance:" in text
    assert "c07" in text


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
    assert "turnaround" in low or "model sheet" in low
    assert "white background" in low
    assert "c02" in text
    assert "больничная рубаха" in text
    assert "watercolor" in low
    assert len(text) <= 4900
    # короткий: реф несёт identity, не простыня
    assert len(text) < 2500
