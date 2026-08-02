"""Гарды активации промтов: нельзя привязать чужой тип файла к слоту."""

from __future__ import annotations

from typing import Any

from app.services.hero_prompt_contract import validate_prompt_variant_for_step
from app.services.prompt_library import (
    is_excel_gpt_prompt_step,
    read_prompt,
    resolve_excel_gpt_prompt_path,
)


def guard_prompt_override_value(step_code: str, name: str) -> str | None:
    """None если ок; иначе текст ошибки для HTTP 400."""
    clean = (name or "").strip()
    if not clean or not step_code:
        return None
    try:
        if is_excel_gpt_prompt_step(step_code):
            path = resolve_excel_gpt_prompt_path(clean)
            if not path.is_file():
                return None
            body = path.read_text(encoding="utf-8")
        else:
            body = read_prompt(step_code, clean)
    except (OSError, FileNotFoundError, ValueError):
        return None
    return validate_prompt_variant_for_step(step_code, body, source_name=clean)


def guard_prompt_overrides(overrides: dict[str, Any] | None) -> str | None:
    if not isinstance(overrides, dict):
        return None
    for step_code, name in overrides.items():
        if not isinstance(step_code, str) or not isinstance(name, str):
            continue
        err = guard_prompt_override_value(step_code, name)
        if err:
            return err
    return None


def guard_prompt_slot_variants(meta: dict[str, Any] | None) -> str | None:
    """Проверить meta.prompt_slot_variants: слот `style` всегда = hero_style."""
    if not isinstance(meta, dict):
        return None
    slots_root = meta.get("prompt_slot_variants")
    if not isinstance(slots_root, dict):
        return None
    for _node_key, slots in slots_root.items():
        if not isinstance(slots, dict):
            continue
        style_name = slots.get("style")
        if isinstance(style_name, str) and style_name.strip():
            err = guard_prompt_override_value("hero_style", style_name.strip())
            if err:
                return err
    return None
