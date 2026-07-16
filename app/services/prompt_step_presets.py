"""Пресеты blocks v2 для legacy-вариантов промта (prompts/<step>/*.md).

Файлы: prompts/step-presets/<step_code>.json
Ключ step_code совпадает с Telegram/menu (script, plan, …) и legacy-ключом
в project.prompt_overrides.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.prompt_library import STEP_FOLDERS
from app.services.prompt_paths import first_existing_under_prompts, user_prompt_file

STEP_PRESETS_REL = "step-presets"


def _preset_read_path(step_code: str) -> Path | None:
    return first_existing_under_prompts(STEP_PRESETS_REL, f"{step_code}.json")


def _preset_write_path(step_code: str) -> Path:
    return user_prompt_file(STEP_PRESETS_REL, f"{step_code}.json")


def load_step_presets(step_code: str) -> dict[str, Any] | None:
    path = _preset_read_path(step_code)
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("prompt_step_presets: bad file {}: {}", path, e)
        return None
    data["step_code"] = step_code
    return data


def list_step_preset_steps() -> list[str]:
    stems: set[str] = set()
    from app.services.prompt_paths import BUNDLED_PROMPTS_ROOT, user_prompts_root

    for root in (user_prompts_root(), BUNDLED_PROMPTS_ROOT):
        d = root / STEP_PRESETS_REL
        if d.is_dir():
            stems.update(p.stem for p in d.glob("*.json") if p.is_file())
    return sorted(stems)


def _alias_map(data: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    presets = data.get("presets") or {}
    if not isinstance(presets, dict):
        return out
    for preset_id, preset in presets.items():
        if not isinstance(preset, dict):
            continue
        out[preset_id] = preset_id
        for alias in preset.get("aliases") or []:
            if isinstance(alias, str) and alias.strip():
                out[alias.strip()] = preset_id
    return out


def resolve_prompt_preset(step_code: str, prompt_name: str) -> dict[str, Any] | None:
    """Найти пресет по имени .md-файла (без расширения)."""
    data = load_step_presets(step_code)
    if not data:
        return None
    presets = data.get("presets") or {}
    if not isinstance(presets, dict):
        return None
    preset_id = _alias_map(data).get(prompt_name.strip())
    if not preset_id:
        return None
    preset = presets.get(preset_id)
    if not isinstance(preset, dict):
        return None
    merged = dict(preset)
    merged["id"] = preset_id
    merged["step_code"] = step_code
    if data.get("compose_step_id"):
        merged["compose_step_id"] = data["compose_step_id"]
    return merged


def apply_prompt_presets_from_overrides(
    overrides: dict[str, Any],
    blocks: dict[str, Any],
    vars_: dict[str, Any],
) -> None:
    """Подмешать пресеты по legacy-ключам шага (до явных po.blocks)."""
    for step_code in STEP_FOLDERS:
        variant = overrides.get(step_code)
        if not isinstance(variant, str) or not variant.strip():
            continue
        preset = resolve_prompt_preset(step_code, variant.strip())
        if not preset:
            continue
        preset_blocks = preset.get("blocks") or {}
        if isinstance(preset_blocks, dict):
            blocks.update(preset_blocks)
        extra = preset.get("extra_blocks") or {}
        if isinstance(extra, dict):
            blocks.update(extra)
        for cat in preset.get("omit_slots") or []:
            if isinstance(cat, str):
                blocks[cat] = {"text": "", "weight": 0}
        preset_vars = preset.get("vars") or {}
        if isinstance(preset_vars, dict):
            vars_.update(preset_vars)


def _write_step_presets(step_code: str, data: dict[str, Any]) -> None:
    path = _preset_write_path(step_code)
    out = {k: v for k, v in data.items() if k != "step_code"}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_step_preset(
    step_code: str,
    preset_id: str,
    *,
    label: str | None = None,
    description: str | None = None,
    blocks: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = load_step_presets(step_code)
    if not data:
        raise FileNotFoundError(f"no presets for step: {step_code}")
    presets = data.get("presets") or {}
    if not isinstance(presets, dict) or preset_id not in presets:
        raise FileNotFoundError(f"preset not found: {step_code}/{preset_id}")
    preset = presets[preset_id]
    if not isinstance(preset, dict):
        raise ValueError(f"invalid preset: {preset_id}")
    if label is not None:
        preset["label"] = label.strip() or preset_id
    if description is not None:
        preset["description"] = description.strip()
    if blocks:
        current = dict(preset.get("blocks") or {})
        omit = list(preset.get("omit_slots") or [])
        for kind, block_id in blocks.items():
            if not isinstance(kind, str):
                continue
            k = kind.strip()
            if not k:
                continue
            if block_id is None or (isinstance(block_id, str) and not block_id.strip()):
                current.pop(k, None)
                # Снять блок с категории, но не прятать слот (omit только из шаблона пресета).
                omit = [slot for slot in omit if slot != k]
                continue
            if not isinstance(block_id, str):
                continue
            bid = block_id.strip()
            if k and bid:
                current[k] = bid
                omit = [slot for slot in omit if slot != k]
        preset["blocks"] = current
        preset["omit_slots"] = omit
    presets[preset_id] = preset
    data["presets"] = presets
    _write_step_presets(step_code, data)
    merged = dict(preset)
    merged["id"] = preset_id
    merged["step_code"] = step_code
    if data.get("compose_step_id"):
        merged["compose_step_id"] = data["compose_step_id"]
    return merged


def create_step_preset(
    step_code: str,
    preset_id: str,
    *,
    label: str | None = None,
    description: str | None = None,
    blocks: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = load_step_presets(step_code)
    if not data:
        raise FileNotFoundError(f"no presets for step: {step_code}")
    presets = data.get("presets") or {}
    if not isinstance(presets, dict):
        raise ValueError(f"invalid presets file: {step_code}")
    clean_id = preset_id.strip()
    if not clean_id:
        raise ValueError("preset id is required")
    if clean_id in presets:
        raise ValueError(f"preset already exists: {step_code}/{clean_id}")
    presets[clean_id] = {
        "label": (label or clean_id).strip() or clean_id,
        "description": (description or "").strip(),
        "blocks": blocks or {},
    }
    order = data.get("preset_order")
    if isinstance(order, list):
        order.append(clean_id)
        data["preset_order"] = order
    data["presets"] = presets
    _write_step_presets(step_code, data)
    return resolve_prompt_preset(step_code, clean_id) or {
        "id": clean_id,
        "step_code": step_code,
        **presets[clean_id],
    }


def delete_step_preset(step_code: str, preset_id: str) -> dict[str, Any]:
    data = load_step_presets(step_code)
    if not data:
        raise FileNotFoundError(f"no presets for step: {step_code}")
    presets = data.get("presets") or {}
    if not isinstance(presets, dict) or preset_id not in presets:
        raise FileNotFoundError(f"preset not found: {step_code}/{preset_id}")
    if preset_id == "default":
        raise ValueError("default preset cannot be deleted")
    del presets[preset_id]
    order = data.get("preset_order")
    if isinstance(order, list):
        data["preset_order"] = [x for x in order if x != preset_id]
    data["presets"] = presets
    _write_step_presets(step_code, data)
    return {"step_code": step_code, "id": preset_id, "deleted": True}
