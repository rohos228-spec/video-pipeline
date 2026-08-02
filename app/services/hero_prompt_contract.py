"""Контракты слотов hero / hero_style.

Частые ошибки:
- в prompt_overrides['hero'] кладут агент реестра (excel_gpt);
- в prompt_overrides['hero_style'] кладут шаблон model sheet / Excel-поля
  или упакованный pack_last с чужим содержимым под «стильным» именем.
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

# Шаблон «собери sheet из реестра Excel» — это НЕ визуальный стиль.
_SHEET_TEMPLATE_MARKERS = (
    "назначение",
    "реестр сущностей",
    "поля персонажа",
    "для заполнения шаблона",
    "не использовать номера строк",
    "номера колонок",
    "шаблон для создания character model sheet",
    "на основе данных персонажа из реестра",
    "листы excel",
)

_STYLE_MARKERS = (
    "style only",
    "visual style",
    "art style",
    "character style",
    "style lock",
    "едином стиле",
    "в едином стиле",
    "стиль —",
    "стиль -",
    "pixel art",
    "пиксель",
    "watercolor",
    "акварел",
    "trash polka",
    "треш польк",
    "полька",
    "noir",
    "grunge",
    "cinematic",
    "illustration",
    "иллюстрац",
    "palette",
    "палитр",
    "master_visual_prompt",
    "rendering",
    "aesthetic",
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


def hero_style_looks_like_sheet_template(text: str) -> bool:
    """Excel/реестр-шаблон сборки sheet — нельзя класть в hero_style."""
    t = (text or "").lower()
    if not t.strip():
        return False
    hits = sum(1 for m in _SHEET_TEMPLATE_MARKERS if m.lower() in t)
    if hits >= 2:
        return True
    # Короткий smoking gun: «НАЗНАЧЕНИЕ» + model sheet / реестр.
    if "назначение" in t and (
        "model sheet" in t or "turnaround" in t or "реестр" in t
    ):
        return True
    return False


def hero_style_looks_like_style(text: str) -> bool:
    t = (text or "").lower()
    if not t.strip():
        return False
    return sum(1 for m in _STYLE_MARKERS if m.lower() in t) >= 2


def validate_hero_style_or_error(text: str, *, source_name: str = "") -> str | None:
    """None если ок; иначе человекочитаемая ошибка для слота hero_style."""
    raw = (text or "").strip()
    where = f" ({source_name})" if source_name else ""
    if len(raw) < 40:
        return (
            f"слот hero_style{where} пустой/слишком короткий — нужен "
            f"визуальный стиль (Trash Polka / watercolor / pixel art…), "
            f"не шаблон sheet и не агент реестра"
        )
    if hero_master_looks_like_registry_agent(raw):
        return (
            f"в слоте hero_style{where} лежит АГЕНТ РЕЕСТРА персонажей. "
            f"Агент → excel_gpt. Стиль → отдельный visual style lock."
        )
    if hero_style_looks_like_sheet_template(raw):
        return (
            f"в слоте hero_style{where} лежит ШАБЛОН model sheet / Excel-поля "
            f"(НАЗНАЧЕНИЕ, реестр сущностей), а не визуальный стиль. "
            f"Sheet → слот hero. Стиль → Archival Noir / Trash Polka / pixel…"
        )
    if not hero_style_looks_like_style(raw):
        # Sheet без style-маркеров тоже нельзя маскировать под стиль.
        if hero_master_looks_like_sheet(raw):
            return (
                f"в слоте hero_style{where} лежит turnaround/model sheet "
                f"без визуального стиля. Sheet → hero; стиль → hero_style."
            )
        return (
            f"слот hero_style{where} не похож на визуальный стиль: нет маркеров "
            f"STYLE ONLY / watercolor / pixel art / trash polka / cinematic…"
        )
    return None


def validate_prompt_variant_for_step(
    step_code: str, text: str, *, source_name: str = ""
) -> str | None:
    """Контракт содержимого при активации варианта. None = ок / шаг без контракта."""
    code = (step_code or "").strip()
    if code == "hero":
        return validate_hero_master_or_error(text, source_name=source_name)
    if code == "hero_style":
        return validate_hero_style_or_error(text, source_name=source_name)
    return None
