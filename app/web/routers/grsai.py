"""REST для Grsai: статус, каталог, quote цены, генерация image/video из Create."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.bots.grsai import (
    GRSAI_AUDIO_CATALOG,
    GRSAI_IMAGE_CATALOG,
    GRSAI_VIDEO_CATALOG,
    GRSAI_WIRED_AUDIO_MODELS,
    GRSAI_WIRED_IMAGE_MODELS,
    GRSAI_WIRED_VIDEO_MODELS,
    generate_audio,
    generate_image,
    generate_video,
    grsai_audio_enabled,
    grsai_enabled,
    grsai_key_configured,
    grsai_video_enabled,
)
from app.services.create_jobs import enqueue_generation, get_job, queue_snapshot
from app.services.grsai_pricing import TOKEN_USD, quote_generation
from app.settings import settings

router = APIRouter(prefix="/grsai", tags=["grsai"])


class GrsaiGenerateBody(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str | None = None
    aspect: str = "9:16"
    resolution: str = "1K"
    media: str = "image"  # image | video | audio
    duration: int | None = 10
    size: str = "small"  # sora: small|large


class GrsaiQuoteBody(BaseModel):
    media: str = "image"
    model: str = "gpt-image-2"
    resolution: str | None = "1K"
    duration: int | None = 10
    size: str | None = "small"
    catalog_price: str | None = None


def _model_dict(m: Any) -> dict[str, Any]:
    return {
        "slug": m.slug,
        "display_name": m.display_name,
        "wired": m.wired,
        "family": m.family,
        "media": m.media,
        "resolutions": list(m.resolutions),
        "aspects": list(m.aspects),
        "durations": list(m.durations),
        "sizes": list(m.sizes),
        "badge": "+",
    }


@router.get("/status")
async def grsai_status() -> dict[str, Any]:
    key = (settings.grsai_api_key or "").strip()
    active_snap = queue_snapshot(provider="grsai")
    return {
        "enabled": grsai_enabled(),
        "video_enabled": grsai_video_enabled(),
        "audio_enabled": grsai_audio_enabled(),
        "configured": grsai_key_configured(),
        "provider": settings.image_provider,
        "video_provider": getattr(settings, "video_provider", "grsai"),
        "base_url": settings.grsai_base_url,
        "default_model": settings.grsai_default_image_model,
        "default_video_model": getattr(settings, "grsai_default_video_model", "sora-2"),
        "key_suffix": (f"…{key[-6:]}" if len(key) >= 6 else None),
        "wired_models": list(GRSAI_WIRED_IMAGE_MODELS),
        "wired_video_models": list(GRSAI_WIRED_VIDEO_MODELS),
        "wired_audio_models": list(GRSAI_WIRED_AUDIO_MODELS),
        "token_usd": TOKEN_USD,
        "queue": active_snap["total_active"],
        "running_count": active_snap["running_count"],
        "waiting_count": active_snap["waiting_count"],
        "max_parallel": active_snap["max_parallel"],
        "active_jobs": active_snap["jobs"],
        "audio_note": (
            None
            if GRSAI_WIRED_AUDIO_MODELS
            else "На Grsai сейчас нет audio-моделей (Suno/TTS) — в Create аудио идёт через пайплайн ElevenLabs/Suno"
        ),
    }


@router.get("/models")
async def grsai_models() -> dict[str, Any]:
    return {
        "models": [_model_dict(m) for m in GRSAI_IMAGE_CATALOG],
        "video_models": [_model_dict(m) for m in GRSAI_VIDEO_CATALOG],
        "audio_models": [_model_dict(m) for m in GRSAI_AUDIO_CATALOG],
    }


@router.post("/quote")
async def grsai_quote(body: GrsaiQuoteBody) -> dict[str, Any]:
    """Цена за выбранные параметры: 1 ток = $0.10."""
    return quote_generation(
        media=body.media,
        model=body.model,
        resolution=body.resolution,
        duration=body.duration,
        size=body.size,
        catalog_price=body.catalog_price,
    )


@router.get("/quote")
async def grsai_quote_get(
    media: str = "image",
    model: str = "gpt-image-2",
    resolution: str = "1K",
    duration: int = 10,
    size: str = "small",
    catalog_price: str | None = None,
) -> dict[str, Any]:
    return quote_generation(
        media=media,
        model=model,
        resolution=resolution,
        duration=duration,
        size=size,
        catalog_price=catalog_price,
    )


@router.get("/jobs/{job_id}")
async def grsai_job_status(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.to_dict()


@router.post("/generate")
async def grsai_generate(body: GrsaiGenerateBody) -> dict[str, Any]:
    if not grsai_key_configured():
        raise HTTPException(status_code=400, detail="GRSAI_API_KEY не задан в .env")

    media = (body.media or "image").strip().lower()
    params = {
        "aspect": body.aspect,
        "resolution": body.resolution,
        "duration": body.duration,
        "size": body.size,
    }
    quote: dict[str, Any] | None = None

    if media == "video":
        model = (
            body.model
            or getattr(settings, "grsai_default_video_model", None)
            or "sora-2"
        ).strip()
        quote = quote_generation(
            media="video",
            model=model,
            resolution=body.resolution,
            duration=body.duration,
            size=body.size,
        )

        async def run(out_path):
            return await generate_video(
                body.prompt,
                out_path,
                model_slug=model,
                aspect_ratio=body.aspect,
                duration=body.duration,
                size=body.size,
                timeout=900,
            )

        job = await enqueue_generation(
            media="video",
            model=model,
            provider="grsai",
            prompt=body.prompt,
            ext=".mp4",
            params=params,
            quote=quote,
            run=run,
        )
    elif media == "audio":
        model = (body.model or "audio").strip()

        async def run(out_path):
            return await generate_audio(body.prompt, out_path, model_slug=model)

        job = await enqueue_generation(
            media="audio",
            model=model,
            provider="grsai",
            prompt=body.prompt,
            ext=".mp3",
            params=params,
            quote=None,
            run=run,
        )
    else:
        model = (body.model or settings.grsai_default_image_model or "gpt-image-2").strip()
        quote = quote_generation(
            media="image",
            model=model,
            resolution=body.resolution,
            duration=body.duration,
            size=body.size,
        )

        async def run(out_path):
            return await generate_image(
                body.prompt,
                out_path,
                model_slug=model,
                aspect_ratio=body.aspect,
                resolution=body.resolution,
                timeout=600,
            )

        job = await enqueue_generation(
            media="image",
            model=model,
            provider="grsai",
            prompt=body.prompt,
            ext=".png",
            params=params,
            quote=quote,
            run=run,
        )

    payload = job.to_dict()
    snap = queue_snapshot(provider="grsai")
    payload["queue"] = snap["total_active"]
    payload["waiting_count"] = snap["waiting_count"]
    payload["running_count"] = snap["running_count"]
    payload["max_parallel"] = snap["max_parallel"]
    payload["quote"] = quote
    return payload
