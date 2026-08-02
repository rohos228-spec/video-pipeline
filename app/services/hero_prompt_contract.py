"""Контракт слота hero: turnaround sheet, НЕ агент реестра персонажей.

Частая ошибка: в prompt_overrides['hero'] кладут
«агент по созданию персонажей» (excel_gpt / apply-ops по закадру).
Тогда GPT импровизирует слабый sheet без dense layout — c01 «хуже» c02.
"""

from __future__ import annotations

_REGISTRY_MARKERS = (
    "frame_uuid",
    "реестр персонаж",
    "заполнения реестра",
    '"characters"',
    '"ops"',
    "персонажи кадров",
    "apply-ops",
    "не выдумывай uuid",
)

_SHEET_MARKERS = (
    "turnaround",
    "model sheet",
    "character sheet",
    "16:9",
    "16/9",
    "white background",
    "#ffffff",
    "full-body",
    "full body",
    "head turnaround",
    "orthographic",
)


def hero_master_looks_like_registry_agent(text: str) -> bool:
    t = (text or "").lower()
    if not t.strip():
        return False
    reg = sum(1 for m in _REGISTRY_MARKERS if m.lower() in t)
    sheet = sum(1 for m in _SHEET_MARKERS if m.lower() in t)
    return reg >= 2 and sheet < 2


def hero_master_looks_like_sheet(text: str) -> bool:
    t = (text or "").lower()
    sheet = sum(1 for m in _SHEET_MARKERS if m.lower() in t)
    return sheet >= 2 and not hero_master_looks_like_registry_agent(text)


def validate_hero_master_or_error(text: str, *, source_name: str = "") -> str | None:
    """None если ок; иначе человекочитаемая ошибка."""
    raw = (text or "").strip()
    if len(raw) < 80:
        return (
            "слот hero пустой/слишком короткий — нужен промт turnaround / "
            "model sheet (16:9, white bg, full-body views)"
        )
    if hero_master_looks_like_registry_agent(raw):
        where = f" ({source_name})" if source_name else ""
        return (
            f"в слоте hero{where} лежит АГЕНТ РЕЕСТРА персонажей "
            f"(frame_uuid / characters / закадр), а не лист генерации. "
            f"Агент реестра → excel_gpt. В hero нужен model sheet / turnaround. "
            f"Правила c01 у вариации — нормальная механика, не баг."
        )
    if not hero_master_looks_like_sheet(raw):
        where = f" ({source_name})" if source_name else ""
        return (
            f"слот hero{where} не похож на turnaround sheet: нет маркеров "
            f"16:9 / white background / full-body / model sheet"
        )
    return None
