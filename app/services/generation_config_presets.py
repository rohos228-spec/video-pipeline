"""Именованные пресеты настроек генерации (мастер проекта).

Хранятся в data/generation_config_presets.json — не в git, но переживают
обновления кода. Используются в Web-мастере и Telegram при создании проекта.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.settings import settings

PRESET_FIELDS: tuple[str, ...] = (
    "image_generator",
    "aspect_ratio",
    "image_resolution",
    "image_quality",
    "image_relax",
    "video_generator",
    "video_resolution",
    "video_relax",
)

_BOOL_FIELDS = frozenset({"image_relax", "video_relax"})


def _presets_path() -> Path:
    path = settings.data_dir / "generation_config_presets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slugify_name(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "preset"
    return base


def _load_raw() -> list[dict[str, Any]]:
    path = _presets_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("generation_config_presets: read failed: {}", e)
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict) and isinstance(data.get("presets"), list):
        return [x for x in data["presets"] if isinstance(x, dict)]
    return []


def _save_raw(items: list[dict[str, Any]]) -> None:
    path = _presets_path()
    path.write_text(
        json.dumps({"presets": items}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def normalize_settings(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in PRESET_FIELDS:
        if field not in raw:
            continue
        val = raw[field]
        if field in _BOOL_FIELDS:
            if val is None:
                continue
            if isinstance(val, str):
                out[field] = val.lower() in ("yes", "true", "1", "on")
            else:
                out[field] = bool(val)
        elif val is not None and val != "":
            out[field] = str(val)
    return out


def settings_from_project(project: Any) -> dict[str, Any]:
    raw = {f: getattr(project, f, None) for f in PRESET_FIELDS}
    return normalize_settings(raw)


def list_presets() -> list[dict[str, Any]]:
    items = _load_raw()
    out: list[dict[str, Any]] = []
    for item in items:
        pid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not pid or not name:
            continue
        settings_dict = normalize_settings(item.get("settings") or {})
        out.append(
            {
                "id": pid,
                "name": name,
                "settings": settings_dict,
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
            }
        )
    out.sort(key=lambda x: x["name"].lower())
    return out


def get_preset(preset_id: str) -> dict[str, Any] | None:
    for p in list_presets():
        if p["id"] == preset_id:
            return p
    return None


def create_preset(name: str, settings: dict[str, Any]) -> dict[str, Any]:
    clean_name = (name or "").strip()
    if not clean_name:
        raise ValueError("name is required")
    norm = normalize_settings(settings)
    if not norm.get("image_generator") or not norm.get("video_generator"):
        raise ValueError("settings must include image_generator and video_generator")

    items = _load_raw()
    base_id = _slugify_name(clean_name)
    preset_id = base_id
    n = 2
    existing_ids = {str(x.get("id")) for x in items}
    while preset_id in existing_ids:
        preset_id = f"{base_id}-{n}"
        n += 1

    now = _now_iso()
    record = {
        "id": preset_id,
        "name": clean_name,
        "settings": norm,
        "created_at": now,
        "updated_at": now,
    }
    items.append(record)
    _save_raw(items)
    return {
        "id": preset_id,
        "name": clean_name,
        "settings": norm,
        "created_at": now,
        "updated_at": now,
    }


def update_preset(
    preset_id: str,
    *,
    name: str | None = None,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items = _load_raw()
    found: dict[str, Any] | None = None
    for item in items:
        if str(item.get("id")) == preset_id:
            found = item
            break
    if found is None:
        raise KeyError(f"preset not found: {preset_id}")

    if name is not None:
        clean = name.strip()
        if not clean:
            raise ValueError("name is required")
        found["name"] = clean
    if settings is not None:
        found["settings"] = normalize_settings(settings)
    found["updated_at"] = _now_iso()
    _save_raw(items)
    return get_preset(preset_id) or found


def delete_preset(preset_id: str) -> bool:
    items = _load_raw()
    new_items = [x for x in items if str(x.get("id")) != preset_id]
    if len(new_items) == len(items):
        return False
    _save_raw(new_items)
    return True


def apply_preset_settings(project: Any, settings: dict[str, Any]) -> None:
    """Записывает поля пресета в Project и skip_value для неприменимых вопросов."""
    from app.telegram import wizard as wiz

    norm = normalize_settings(settings)
    for field, val in norm.items():
        setattr(project, field, val)
    for q in wiz._QUESTIONS:
        if q.skip_if(project) and not q.is_set(project):
            setattr(project, q.field, q.skip_value)


def new_preset_id() -> str:
    return uuid.uuid4().hex[:12]
