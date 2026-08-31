"""REST: индивидуальная генерация через kie.ai (вкладка «Генерация»).

Каталог моделей/настроек/цен — app/services/kie_catalog.py.
Задачи — через общий пул create_jobs (provider="kie"), файлы — в
data/generations (история Create подхватывает автоматически).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from loguru import logger

from app.bots import kie_http
from app.services import kie_catalog
from app.services.create_jobs import enqueue_generation, get_job

router = APIRouter(prefix="/kie-create", tags=["kie-create"])

_EXT_BY_RESULT = {"video": ".mp4", "image": ".png", "audio": ".mp3", "text": ".txt"}
_MEDIA_BY_RESULT = {"video": "video", "image": "image", "audio": "audio", "text": "audio"}


@router.get("/catalog")
async def get_catalog() -> dict[str, Any]:
    """Модели + поля + правила цен для динамической формы."""
    return {
        **kie_catalog.catalog_for_ui(),
        "configured": kie_http.kie_configured(),
    }


def _key_debug() -> dict[str, Any]:
    """Отпечаток ключа для диагностики 401 (маскированный, безопасно)."""
    from app.bots.kie_kling import kie_api_key, kie_api_key_source

    key = kie_api_key()
    src = kie_api_key_source() or ("пусто" if not key else "неизвестно")
    fp = f"{key[:4]}…{key[-4:]}" if len(key) >= 10 else ("пусто" if not key else "короткий!")
    return {
        "key_source": src,
        "key_fingerprint": fp,
        "base_url": kie_http.kie_api_base_url(),
    }


@router.get("/credits")
async def get_credits() -> dict[str, Any]:
    credits = await kie_http.get_credits()
    return {
        "configured": kie_http.kie_configured(),
        "credits": credits,
        "usd": round(credits * kie_catalog.CREDIT_USD, 2) if credits is not None else None,
        **_key_debug(),
    }


@router.post("/estimate")
async def post_estimate(body: dict[str, Any]) -> dict[str, Any]:
    spec = kie_catalog.get_model(str((body or {}).get("model_id") or ""))
    if spec is None:
        raise HTTPException(status_code=404, detail="unknown model_id")
    values = (body or {}).get("values") or {}
    return kie_catalog.estimate_credits(spec, values)


@router.post("/upload")
async def post_upload(file: UploadFile = File(...)) -> dict[str, Any]:  # noqa: B008
    """Референс-файл → публичный URL kie (для image/video/audio полей)."""
    if not kie_http.kie_configured():
        raise HTTPException(status_code=503, detail="kie API не настроен (KIE_API_KEY)")
    content = await file.read()
    if not content or len(content) < 16:
        raise HTTPException(status_code=400, detail="пустой файл")
    if len(content) > 200 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="файл > 200 МБ")
    name = Path(file.filename or "file.bin").name
    name = re.sub(r"[^\w.\-]+", "_", name)[:80] or "file.bin"
    url = await kie_http.upload_file(content, name)
    return {"url": url, "filename": name, "bytes": len(content)}


def _find_text(obj: Any) -> str:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if (
                str(k).lower() in ("lyrics", "text", "content", "lyric")
                and isinstance(v, str)
                and v.strip()
            ):
                return v
        for v in obj.values():
            found = _find_text(v)
            if found:
                return found
    elif isinstance(obj, list):
        for it in obj:
            found = _find_text(it)
            if found:
                return found
    return ""


@router.post("/generate")
async def post_generate(body: dict[str, Any]) -> dict[str, Any]:
    model_id = str((body or {}).get("model_id") or "").strip()
    spec = kie_catalog.get_model(model_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"unknown model_id {model_id!r}")
    values = (body or {}).get("values") or {}
    if not isinstance(values, dict):
        raise HTTPException(status_code=400, detail="values must be object")
    errors = kie_catalog.validate_values(spec, values)
    if errors:
        raise HTTPException(status_code=422, detail="; ".join(errors[:6]))
    if not kie_http.kie_configured():
        raise HTTPException(status_code=503, detail="kie API не настроен (KIE_API_KEY)")

    payload = kie_catalog.build_payload(spec, values)
    quote = kie_catalog.estimate_credits(spec, values)
    result_kind = str(spec.get("result") or "video")
    media = _MEDIA_BY_RESULT.get(result_kind, "video")
    ext = _EXT_BY_RESULT.get(result_kind, ".mp4")
    prompt = str(values.get("prompt") or values.get("text") or "")[:2000]

    async def run(out_path: Path):
        if result_kind == "text":
            task_id = await kie_http.create_task(
                str(spec.get("api")), spec.get("endpoint"), payload
            )
            data = await kie_http.poll_task(str(spec.get("api")), task_id)
            text = _find_text(data) or str(data)[:4000]
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text, encoding="utf-8")
            from app.bots.outsee import GenerationResult

            return GenerationResult(file_path=out_path, gen_id=task_id, raw_url=None)
        return await kie_http.run_generation(spec, payload, out_path)

    job = await enqueue_generation(
        media=media,
        model=spec["label"],
        provider="kie",
        prompt=prompt,
        ext=ext,
        params={"model_id": model_id, "values": values},
        quote={"credits": quote["credits"], "usd": quote["usd"], "note": quote["note"]},
        run=run,
    )
    logger.info(
        "kie-create: generate {} ({}) → job {} (~{} кр / ${})",
        model_id,
        spec["label"],
        job.id,
        quote["credits"],
        quote["usd"],
    )
    return {"job": job.to_dict(), "estimate": quote}


@router.get("/jobs/{job_id}")
async def kie_job_status(job_id: str) -> dict[str, Any]:
    job = get_job(job_id)
    if job is None:
        return {"job_id": job_id, "status": "unknown", "ok": False}
    return job.to_dict()
