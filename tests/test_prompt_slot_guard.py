"""Гарды активации hero / hero_style."""

from __future__ import annotations

import pytest

from app.services.prompt_active_global import get_global_active, set_global_active
from app.services.prompt_library import write_prompt
from app.services.prompt_slot_guard import (
    guard_prompt_overrides,
    guard_prompt_slot_variants,
)

_STYLE = (
    "Archival Noir Watercolor Grunge / Trash Polka character style lock. "
    "This block is STYLE ONLY visual style for the sheet."
)
_SHEET_TEMPLATE = """
ШАБЛОН ДЛЯ СОЗДАНИЯ CHARACTER MODEL SHEET / TURNAROUND SHEET
НАЗНАЧЕНИЕ
лист персонажа из реестра сущностей персонажи
поля персонажа id имя внешность
не использовать номера строк и номера колонок Excel
"""


@pytest.fixture
def hero_style_step(tmp_path, monkeypatch):
    prompts_root = tmp_path / "prompts"
    (prompts_root / "04_hero_style").mkdir(parents=True)
    monkeypatch.setattr("app.services.prompt_library.PROMPTS_ROOT", prompts_root)
    monkeypatch.setattr("app.services.prompt_active_global.PROMPTS_ROOT", prompts_root)
    return "hero_style"


def test_guard_rejects_sheet_template_override(hero_style_step):
    write_prompt(hero_style_step, "bad_pack", _SHEET_TEMPLATE)
    write_prompt(hero_style_step, "good_style", _STYLE)
    assert guard_prompt_overrides({"hero_style": "bad_pack"}) is not None
    assert guard_prompt_overrides({"hero_style": "good_style"}) is None


def test_guard_rejects_style_slot_variant(hero_style_step):
    write_prompt(hero_style_step, "bad_pack", _SHEET_TEMPLATE)
    err = guard_prompt_slot_variants(
        {"prompt_slot_variants": {"n_hero": {"style": "bad_pack"}}}
    )
    assert err is not None


def test_set_global_active_refuses_bad_style(hero_style_step):
    write_prompt(hero_style_step, "bad_pack", _SHEET_TEMPLATE)
    write_prompt(hero_style_step, "good_style", _STYLE)
    set_global_active(hero_style_step, "bad_pack")
    assert get_global_active(hero_style_step) is None
    set_global_active(hero_style_step, "good_style")
    assert get_global_active(hero_style_step) == "good_style"
