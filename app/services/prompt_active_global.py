"""Глобально активные варианты промтов (prompts/active_variants.json).

Последний сохранённый/активированный .md для шага — общий для всех проектов,
пока у проекта нет своего prompt_overrides[step_code].
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.prompt_library import PROMPTS_ROOT, prompt_path

_ACTIVE_FILE = "active_variants.json"


def _active_path() -> Path:
    path = PROMPTS_ROOT / _ACTIVE_FILE
    PROMPTS_ROOT.mkdir(parents=True, exist_ok=True)
    return path


def load_global_active() -> dict[str, str]:
    path = _active_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("prompt_active_global: bad {}: {}", path, e)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def _save_global_active(data: dict[str, str]) -> None:
    path = _active_path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_global_active(step_code: str) -> str | None:
    name = load_global_active().get(step_code)
    if not name:
        return None
    try:
        if prompt_path(step_code, name).is_file():
            return name
    except ValueError:
        return None
    return None


def set_global_active(step_code: str, name: str) -> None:
    from app.services.prompt_library import ENRICH_STEP_CODES, EXCEL_GPT_UNIFIED_STEP

    if step_code in ENRICH_STEP_CODES:
        step_code = EXCEL_GPT_UNIFIED_STEP
    clean = (name or "").strip()
    if not clean:
        return
    try:
        if not prompt_path(step_code, clean).is_file():
            return
    except ValueError:
        return
    data = load_global_active()
    data[step_code] = clean
    _save_global_active(data)
    logger.debug("prompt_active_global: {} → {!r}", step_code, clean)


def sync_global_active_from_overrides(overrides: dict[str, Any] | None) -> None:
    if not overrides:
        return
    from app.services.prompt_library import ENRICH_STEP_CODES, EXCEL_GPT_UNIFIED_STEP, STEP_FOLDERS

    for step_code, name in overrides.items():
        if not isinstance(name, str):
            continue
        if step_code in ENRICH_STEP_CODES:
            set_global_active(EXCEL_GPT_UNIFIED_STEP, name.strip())
            continue
        if step_code in STEP_FOLDERS:
            set_global_active(step_code, name.strip())

