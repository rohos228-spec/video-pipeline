"""REST: сохранённые конфигурации генерации (мастер проекта)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.generation_config_presets import (
    PRESET_FIELDS,
    create_preset,
    delete_preset,
    get_preset,
    list_presets,
    normalize_settings,
    update_preset,
)

router = APIRouter(prefix="/generation-config-presets", tags=["config-presets"])


class PresetSettings(BaseModel):
    image_generator: str | None = None
    aspect_ratio: str | None = None
    image_resolution: str | None = None
    image_quality: str | None = None
    image_relax: bool | None = None
    video_generator: str | None = None
    video_resolution: str | None = None
    video_relax: bool | None = None


class CreatePresetRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    settings: PresetSettings


class UpdatePresetRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    settings: PresetSettings | None = None


@router.get("")
async def presets_list() -> dict[str, Any]:
    return {"presets": list_presets(), "fields": list(PRESET_FIELDS)}


@router.get("/{preset_id}")
async def presets_get(preset_id: str) -> dict[str, Any]:
    p = get_preset(preset_id)
    if p is None:
        raise HTTPException(status_code=404, detail="preset not found")
    return p


@router.post("")
async def presets_create(body: CreatePresetRequest) -> dict[str, Any]:
    try:
        return create_preset(body.name, body.settings.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.patch("/{preset_id}")
async def presets_update(preset_id: str, body: UpdatePresetRequest) -> dict[str, Any]:
    try:
        settings = body.settings.model_dump() if body.settings else None
        return update_preset(preset_id, name=body.name, settings=settings)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/{preset_id}")
async def presets_delete(preset_id: str) -> dict[str, bool]:
    ok = delete_preset(preset_id)
    if not ok:
        raise HTTPException(status_code=404, detail="preset not found")
    return {"ok": True}


@router.post("/normalize")
async def presets_normalize(body: PresetSettings) -> dict[str, Any]:
    return {"settings": normalize_settings(body.model_dump())}
