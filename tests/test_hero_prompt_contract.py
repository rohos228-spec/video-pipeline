"""hero slot: sheet vs registry agent."""

from __future__ import annotations

from app.services.hero_prompt_contract import (
    hero_master_looks_like_registry_agent,
    hero_master_looks_like_sheet,
    hero_style_looks_like_character_lock,
    hero_style_looks_like_img_pr_template,
    validate_hero_master_or_error,
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


def test_detect_registry_agent_in_hero_slot() -> None:
    assert hero_master_looks_like_registry_agent(_REGISTRY)
    assert not hero_master_looks_like_sheet(_REGISTRY)
    err = validate_hero_master_or_error(_REGISTRY, source_name="agent.md")
    assert err is not None
    assert "РЕЕСТРА" in err or "реестра" in err.lower()


def test_accept_sheet_master() -> None:
    assert hero_master_looks_like_sheet(_SHEET)
    assert validate_hero_master_or_error(_SHEET) is None


def test_hero_style_img_pr_template_vs_character_lock() -> None:
    img_pr = (
        "Поле `промт_картинки` и `промт_картинки_2`.\n"
        "Не переписывай поле `закадр`.\n"
        "apply-ops JSON.\n"
    )
    lock = (
        "Archival Noir Watercolor character style lock for a turnaround sheet.\n"
        "STYLE_LABEL: Trash Polka.\n"
        "STYLE_CORE: watercolor wash.\n"
        "This block is STYLE ONLY.\n"
    )
    assert hero_style_looks_like_img_pr_template(img_pr)
    assert not hero_style_looks_like_character_lock(img_pr)
    assert hero_style_looks_like_character_lock(lock)
    assert not hero_style_looks_like_img_pr_template(lock)

