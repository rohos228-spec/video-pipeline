"""hero / hero_style: sheet vs style vs registry agent."""

from __future__ import annotations

from app.services.hero_prompt_contract import (
    hero_master_looks_like_registry_agent,
    hero_master_looks_like_sheet,
    hero_style_looks_like_sheet_template,
    hero_style_looks_like_style,
    validate_hero_master_or_error,
    validate_hero_style_or_error,
    validate_prompt_variant_for_step,
)

_REGISTRY = """
Ты — агент заполнения реестра персонажей.
frame_uuid — адрес кадра
закадр — источник
вернуть {"ops":[],"characters":[]}
"""

_SHEET = """
16/9 Create a dense professional character model sheet / turnaround sheet.
pure white background (#FFFFFF)
full body front view, full body side view
head turnaround views
orthographic turnaround feel
"""

_STYLE = """
Archival Noir Watercolor Grunge Dossier / Trash Polka character style lock
for a professional character model sheet / turnaround sheet.

Apply this visual style to the SAME exact character on every panel.
This block is STYLE ONLY: do not invent plot or scene locations.
"""

_SHEET_TEMPLATE = """
ШАБЛОН ДЛЯ СОЗДАНИЯ CHARACTER MODEL SHEET / TURNAROUND SHEET

НАЗНАЧЕНИЕ

Создавать единый профессиональный лист персонажа в формате 16:9
на основе данных персонажа из реестра сущностей `персонажи`.

Для заполнения шаблона используются поля персонажа:
- `id`
- `имя`
- `внешность`

Не использовать номера строк, номера колонок, названия листов Excel.
"""


def test_detect_registry_agent_in_hero_slot() -> None:
    assert hero_master_looks_like_registry_agent(_REGISTRY)
    assert not hero_master_looks_like_sheet(_REGISTRY)
    err = validate_hero_master_or_error(_REGISTRY, source_name="agent.md")
    assert err is not None
    assert "РЕЕСТРА" in err or "реестра" in err.lower()


def test_accept_sheet_master() -> None:
    assert hero_master_looks_like_sheet(_SHEET)
    assert validate_hero_master_or_error(_SHEET) is None


def test_reject_sheet_template_in_hero_style() -> None:
    assert hero_style_looks_like_sheet_template(_SHEET_TEMPLATE)
    assert not hero_style_looks_like_style(_SHEET_TEMPLATE)
    err = validate_hero_style_or_error(_SHEET_TEMPLATE, source_name="pack.txt")
    assert err is not None
    assert "ШАБЛОН" in err or "стиль" in err.lower()


def test_accept_visual_style_in_hero_style() -> None:
    assert hero_style_looks_like_style(_STYLE)
    assert validate_hero_style_or_error(_STYLE) is None


def test_reject_plain_sheet_as_hero_style() -> None:
    err = validate_hero_style_or_error(_SHEET, source_name="sheet.md")
    assert err is not None


def test_step_guard_routes() -> None:
    assert validate_prompt_variant_for_step("hero", _SHEET) is None
    assert validate_prompt_variant_for_step("hero", _REGISTRY) is not None
    assert validate_prompt_variant_for_step("hero_style", _STYLE) is None
    assert validate_prompt_variant_for_step("hero_style", _SHEET_TEMPLATE) is not None
    assert validate_prompt_variant_for_step("plan", _SHEET_TEMPLATE) is None
