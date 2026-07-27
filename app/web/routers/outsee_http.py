"""Studio API: Outsee.io Developer API (Bearer OUTSEE_API_KEY)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.bots import outsee_http as oh
from app.settings import settings

router = APIRouter(prefix="/outsee", tags=["outsee-http"])


class OutseeGenerateBody(BaseModel):
    prompt: str = Field(..., min_length=1)
    media: Literal["image", "video", "audio"] = "image"
    model: str | None = None
    aspect: str | None = "9:16"
    resolution: str | None = None
    duration: int | None = 5
    title: str | None = None  # audio (не поддерживается API)
    relax: bool = False
    generate_audio: bool = False
    project_id: int | None = None


@router.get("/status")
async def outsee_http_status() -> dict[str, Any]:
    key = oh.outsee_api_key()
    balance: dict[str, Any] | None = None
    if oh.outsee_api_configured():
        try:
            balance = await oh.fetch_balance()
        except Exception as e:  # noqa: BLE001
            balance = {"error": str(e)[:200]}
    return {
        "configured": oh.outsee_api_configured(),
        "enabled_image": oh.outsee_api_enabled_for_image(),
        "enabled_video": oh.outsee_api_enabled_for_video(),
        "http_api": oh.outsee_http_enabled(),
        "fallback_cdp": bool(getattr(settings, "outsee_http_fallback_cdp", True)),
        "image_provider": settings.image_provider,
        "video_provider": settings.video_provider,
        "base_url": settings.outsee_api_base_url,
        "default_image_model": settings.outsee_default_image_model,
        "default_video_model": settings.outsee_default_video_model,
        "wired_image_models": list(oh.OUTSEE_WIRED_IMAGE_MODELS),
        "wired_video_models": list(oh.OUTSEE_WIRED_VIDEO_MODELS),
        "key_suffix": (f"…{key[-6:]}" if len(key) >= 6 else None),
        "balance": balance,
        "hint": (
            "OUTSEE_API_KEY из https://outsee.io/profile — Bearer /api/v1 "
            "(отдельный ключ, не Grsai)"
            if oh.outsee_api_configured()
            else "Задай OUTSEE_API_KEY в .env (профиль outsee.io)"
        ),
    }


@router.get("/models")
async def outsee_models() -> dict[str, Any]:
    if not oh.outsee_api_configured():
        raise HTTPException(status_code=400, detail="OUTSEE_API_KEY не задан")
    try:
        data = await oh.fetch_models()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(e)[:400]) from e
    return data


@router.post("/generate")
async def outsee_generate(body: OutseeGenerateBody) -> dict[str, Any]:
    """Прямая генерация через Outsee Developer API (не Grsai, не Chrome)."""
    if not oh.outsee_api_configured():
        raise HTTPException(
            status_code=400,
            detail="OUTSEE_API_KEY не задан — ключ из https://outsee.io/profile",
        )

    text = (body.prompt or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="пустой промпт")

    media = body.media
    if media == "audio":
        raise HTTPException(
            status_code=501,
            detail="Outsee Developer API не поддерживает audio",
        )

    out_dir = Path(settings.data_dir) / "outsee_create"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = uuid.uuid4().hex[:10]

    try:
        if media == "video":
            model = oh.studio_id_to_outsee_video_slug(body.model)
            out_path = out_dir / f"vid_{stamp}.mp4"
            result = await oh.generate_video(
                text,
                out_path,
                model_slug=model,
                aspect_ratio=(body.aspect or "9:16").replace("_", ":"),
                resolution=body.resolution or "720p",
                duration=body.duration,
                project_id=body.project_id,
                timeout=900,
            )
        else:
            model = oh.studio_id_to_outsee_image_slug(body.model)
            out_path = out_dir / f"img_{stamp}.png"
            result = await oh.generate_image(
                text,
                out_path,
                model_slug=model,
                aspect_ratio=(body.aspect or "9:16").replace("_", ":"),
                resolution=body.resolution or "2K",
                project_id=body.project_id,
                timeout=600,
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("outsee.generate failed")
        raise HTTPException(
            status_code=502, detail=str(getattr(e, "reason", None) or e)[:500]
        ) from e

    rel = str(result.file_path)
    preview = f"/api/files?path={result.file_path}"
    return {
        "ok": True,
        "media": media,
        "model": model,
        "path": rel,
        "preview_url": preview,
        "raw_url": result.raw_url,
        "gen_id": result.gen_id,
        "provider": "outsee-api",
        "bytes": result.file_path.stat().st_size if result.file_path.is_file() else 0,
    }
