"""REST для Grsai: статус, каталог wired-моделей, разовая генерация из Create."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.bots.grsai import (
    GRSAI_IMAGE_CATALOG,
    GRSAI_WIRED_IMAGE_MODELS,
    generate_image,
    grsai_enabled,
)
from app.settings import settings

router = APIRouter(prefix="/grsai", tags=["grsai"])


class GrsaiGenerateBody(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str | None = None
    aspect: str = "9:16"
    resolution: str = "1K"


@router.get("/status")
async def grsai_status() -> dict[str, Any]:
    key = (settings.grsai_api_key or "").strip()
    return {
        "enabled": grsai_enabled(),
        "configured": bool(key),
        "provider": settings.image_provider,
        "base_url": settings.grsai_base_url,
        "default_model": settings.grsai_default_image_model,
        "key_suffix": (f"…{key[-6:]}" if len(key) >= 6 else None),
        "wired_models": list(GRSAI_WIRED_IMAGE_MODELS),
    }


@router.get("/models")
async def grsai_models() -> dict[str, Any]:
    return {
        "models": [
            {
                "slug": m.slug,
                "display_name": m.display_name,
                "wired": m.wired,
                "family": m.family,
                "resolutions": list(m.resolutions),
                "aspects": list(m.aspects),
                "badge": "+",
            }
            for m in GRSAI_IMAGE_CATALOG
        ]
    }


@router.post("/generate")
async def grsai_generate(body: GrsaiGenerateBody) -> dict[str, Any]:
    if not (settings.grsai_api_key or "").strip():
        raise HTTPException(status_code=400, detail="GRSAI_API_KEY не задан в .env")
    model = (body.model or settings.grsai_default_image_model or "gpt-image-2").strip()
    out_dir = settings.data_dir / "grsai_history"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{uuid.uuid4().hex}.png"
    try:
        result = await generate_image(
            body.prompt,
            out_path,
            model_slug=model,
            aspect_ratio=body.aspect,
            resolution=body.resolution,
            timeout=600,
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(getattr(e, "reason", None) or e)) from e

    rel = result.file_path
    preview = f"/api/files?path={rel}"
    return {
        "ok": True,
        "model": model,
        "path": str(rel),
        "preview_url": preview,
        "raw_url": result.raw_url,
        "bytes": rel.stat().st_size if rel.is_file() else 0,
    }
