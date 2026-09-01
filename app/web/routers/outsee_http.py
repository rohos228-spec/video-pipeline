"""Studio API: Outsee.io Developer API (Bearer OUTSEE_API_KEY)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.bots import outsee_http as oh
from app.services.create_jobs import enqueue_generation, get_job, queue_snapshot
from app.settings import settings

router = APIRouter(prefix="/outsee", tags=["outsee-http"])


class OutseeGenerateBody(BaseModel):
    prompt: str = Field(..., min_length=1)
    media: Literal["image", "video", "audio"] = "image"
    model: str | None = None
    aspect: str | None = "9:16"
    resolution: str | None = None
    detail_level: str | None = None
    duration: int | None = 5
    title: str | None = None
    relax: bool = False
    generate_audio: bool | None = None
    project_id: int | None = None
    # data: URL или https — стартовый / конечный кадр (Veo) / референс (image)
    first_frame_url: str | None = None
    last_frame_url: str | None = None
    reference_images: list[str] | None = None
    nonce: str | None = None
    batch_index: int | None = None


@router.get("/status")
async def outsee_http_status() -> dict[str, Any]:
    key = oh.outsee_api_key()
    balance: dict[str, Any] | None = None
    if oh.outsee_api_configured():
        try:
            balance = await oh.fetch_balance()
        except Exception as e:  # noqa: BLE001
            balance = {"error": str(e)[:200]}
    active_snap = queue_snapshot(provider="outsee")
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
        "queue": active_snap["total_active"],
        "running_count": active_snap["running_count"],
        "waiting_count": active_snap["waiting_count"],
        "max_parallel": active_snap["max_parallel"],
        "active_jobs": active_snap["jobs"],
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


@router.get("/jobs/{job_id}")
async def outsee_job_status(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.to_dict()


@router.post("/generate")
async def outsee_generate(body: OutseeGenerateBody) -> dict[str, Any]:
    """Ставит генерацию в очередь: сразу pending в истории, файл — позже."""
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

    params = {
        "aspect": body.aspect,
        "resolution": body.resolution,
        "duration": body.duration,
        "project_id": body.project_id,
        "generate_audio": body.generate_audio,
        "has_first_frame": bool(body.first_frame_url),
        "has_last_frame": bool(body.last_frame_url),
        "nonce": body.nonce,
        "batch_index": body.batch_index,
    }

    if media == "video":
        model = oh.studio_id_to_outsee_video_slug(body.model)

        async def run(out_path):
            return await oh.generate_video(
                text,
                out_path,
                model_slug=model,
                aspect_ratio=(body.aspect or "9:16").replace("_", ":"),
                resolution=body.resolution or "720p",
                duration=body.duration,
                generate_audio=body.generate_audio,
                first_frame_url=body.first_frame_url,
                last_frame_url=body.last_frame_url,
                project_id=body.project_id,
                timeout=900,
            )

        job = await enqueue_generation(
            media="video",
            model=model,
            provider="outsee",
            prompt=text,
            ext=".mp4",
            params=params,
            quote=None,
            run=run,
        )
    else:
        try:
            model = oh.studio_id_to_outsee_image_slug(body.model)
        except oh.NanoBananaProOutseeBannedError as e:
            raise HTTPException(status_code=400, detail=str(e.reason)) from e

        async def run(out_path):
            return await oh.generate_image(
                text,
                out_path,
                model_slug=model,
                aspect_ratio=(body.aspect or "9:16").replace("_", ":"),
                resolution=body.resolution or "2K",
                detail_level=body.detail_level,
                reference_images=body.reference_images
                or ([body.first_frame_url] if body.first_frame_url else None),
                project_id=body.project_id,
                timeout=600,
            )

        job = await enqueue_generation(
            media="image",
            model=model,
            provider="outsee",
            prompt=text,
            ext=".png",
            params=params,
            quote=None,
            run=run,
        )

    payload = job.to_dict()
    snap = queue_snapshot(provider="outsee")
    payload["queue"] = snap["total_active"]
    payload["waiting_count"] = snap["waiting_count"]
    payload["running_count"] = snap["running_count"]
    payload["max_parallel"] = snap["max_parallel"]
    return payload
