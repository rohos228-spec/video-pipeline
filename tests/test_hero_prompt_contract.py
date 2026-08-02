"""hero slot: sheet vs registry agent."""

from __future__ import annotations

from app.services.hero_prompt_contract import (
    hero_master_looks_like_registry_agent,
    hero_master_looks_like_sheet,
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
