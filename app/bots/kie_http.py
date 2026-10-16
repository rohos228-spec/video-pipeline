"""Универсальный клиент kie.ai для вкладки «Генерация».

Три семейства API:
  - jobs  : POST /api/v1/jobs/createTask → GET /api/v1/jobs/recordInfo
  - veo   : POST /api/v1/veo/generate   → GET /api/v1/veo/record-info
  - suno  : POST /api/v1/generate[/*]   → GET /api/v1/generate/record-info
  - runway: POST /api/v1/runway/generate → GET /api/v1/runway/record-detail
Плюс загрузка файлов: POST {upload_base}/api/file-stream-upload
и баланс: GET /api/v1/chat/credit.

Auth: Bearer KIE_API_KEY (или GPT_API_KEY), Create через VPS если он есть.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.bots.kie_kling import (
    KieKlingError,
    kie_api_base_url,
    kie_api_key,
    kie_api_key_source,
    kie_auth_headers,
    kie_post_json,
    kie_uses_vps_relay,
)
from app.bots.outsee import GenerationResult
from app.services import media_spend, spend_guard
from app.settings import settings

_POLL_INTERVAL_S = 4.0
_POLL_MAX_S = 1200.0
_UPLOAD_PATH = "/api/file-stream-upload"
_CALLBACK_PLACEHOLDER = "https://localhost/kie-callback"
_AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".flac")
_VIDEO_EXTS = (".mp4", ".webm", ".mov")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")

_POLL_PATHS = {
    "jobs": "/api/v1/jobs/recordInfo",
    "veo": "/api/v1/veo/record-info",
    "suno": "/api/v1/generate/record-info",
    "runway": "/api/v1/runway/record-detail",
}


class KieHttpError(KieKlingError):
    """Ошибка kie Market/Create — совместима с outsee_retry."""


class KieUncertainError(KieHttpError):
    pass


class KieSubmitUnknownError(KieUncertainError):

    def __init__(self, message: str, *, context: dict[str, Any] | None = None):
        super().__init__(message, context={**(context or {}), "uncertain": True, "no_resubmit": True,
                                           "submit_unknown": True})


class KieTaskPending(KieUncertainError):

    def __init__(self, message: str, *, task_id: str, kind: str = "timeout", context: dict[str, Any] | None = None):
        super().__init__(message, context={"provider_code": 504, **(context or {}), "task_id": task_id,
                                           "kind": kind, "no_resubmit": True, "pending": True})


def kie_configured() -> bool:
    return bool(kie_api_key())


def _headers() -> dict[str, str]:
    return kie_auth_headers()


def _check(payload: Any, *, http_status: int, where: str) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    code = data.get("code", http_status)
    try:
        code_i = int(code) if code is not None else http_status
    except (TypeError, ValueError):
        code_i = http_status
    if code_i in (None, 200):
        return data
    msg = str(data.get("msg") or data.get("message") or where)
    if code_i == 401:
        src = kie_api_key_source() or "нет"
        via = "VPS" if kie_uses_vps_relay() else kie_api_base_url()
        logger.warning(
            "kie 401 {} key_source={} base={} via_vps={}",
            where,
            src,
            kie_api_base_url(),
            kie_uses_vps_relay(),
        )
        msg += f" — ключ {src}, запрос через {via}"
    raise KieHttpError(
        f"kie {where}: code={code_i} {msg}"[:400],
        context={"provider_code": code_i, "kie_msg": msg[:200]},
    )


async def get_credits() -> float | None:
    """Баланс кредитов аккаунта (None — если не настроено/ошибка)."""
    if not kie_configured():
        return None
    url = f"{kie_api_base_url()}/api/v1/chat/credit"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, headers=_headers())
            payload = r.json()
        data = _check(payload, http_status=r.status_code, where="credit")
        val = data.get("data")
        return float(val) if val is not None else None
    except Exception as e:  # noqa: BLE001
        logger.warning("kie credits: {}", e)
        return None


async def upload_file(
    content: bytes, filename: str, *, upload_path: str = "images/studio-create"
) -> str:
    """Файл → публичный URL kie (tempfile.redpandaai.co, живёт ~3 суток)."""
    url = f"{settings.kie_upload_base_url.rstrip('/')}{_UPLOAD_PATH}"
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(
            url,
            headers=_headers(),
            files={"file": (filename, content)},
            data={"uploadPath": upload_path, "fileName": filename},
        )
        try:
            payload = r.json()
        except Exception:  # noqa: BLE001
            payload = {"msg": (r.text or "")[:300], "code": r.status_code}
    data = _check(payload, http_status=r.status_code, where="upload")
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    dl = str(inner.get("downloadUrl") or inner.get("url") or "").strip()
    if not dl.startswith("http"):
        raise KieHttpError(
            "kie upload: нет downloadUrl в ответе",
            context={"provider_code": 500, "raw": str(payload)[:200]},
        )
    return dl


def _create_estimate(_args: tuple, _kwargs: dict) -> float | None:
    try:
        usd = float((media_spend._kie_ctx.get() or {}).get("usd") or 0.0)
    except (TypeError, ValueError):
        return None
    return usd if usd > 0 else None


def _blocked_as_kie(blocked: spend_guard.PaidCallBlocked) -> KieHttpError:
    return KieHttpError(blocked.message_ru, context=dict(blocked.context))


@spend_guard.paid_seam("kie", "createTask", estimate=_create_estimate, wrap_error=_blocked_as_kie)
@media_spend.tracked_kie_create
async def create_task(api: str, endpoint: str | None, body: dict[str, Any]) -> str:
    if api == "jobs":
        path = "/api/v1/jobs/createTask"
    else:
        path = (endpoint or "").strip()
        if not path:
            raise KieHttpError(
                f"kie: нет endpoint для api={api}",
                context={"provider_code": 500},
            )
        if api in ("suno", "runway") and "callBackUrl" not in body:
            # suno/runway требуют callBackUrl — мы поллим, колбэк-заглушка
            body = {**body, "callBackUrl": _CALLBACK_PLACEHOLDER}
    url = f"{kie_api_base_url()}{path}"
    from app.services.media_transport import current_job, json_request
    if current_job.get() is not None:
        r = await json_request("POST", path, payload=body, headers=_headers())
        payload = r.json()
    else:
        r, payload, _src = await kie_post_json(url, body, timeout=120.0)
    data = _check(payload, http_status=r.status_code, where=f"create {path}")
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    task_id = str(
        inner.get("taskId") or inner.get("task_id") or inner.get("id") or ""
    ).strip()
    if not task_id:
        raise KieHttpError(
            f"kie create {path}: ответ без taskId",
            context={"provider_code": 500, "raw": str(payload)[:200], "no_task_id": True},
        )
    return task_id


def _extract_urls(obj: Any, out: list[str] | None = None) -> list[str]:
    """Рекурсивно собрать все http(s) ссылки на медиа из ответа задачи."""
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in ("resulturls",) and isinstance(v, list):
                for u in v:
                    if str(u).startswith("http"):
                        out.append(str(u))
            elif lk in ("audiourl", "videourl", "imageurl", "streamaudiourl",
                        "sourceimageurl", "resulturl", "downloadurl", "url"):
                if isinstance(v, str) and v.startswith("http"):
                    out.append(v)
            else:
                _extract_urls(v, out)
    elif isinstance(obj, list):
        for it in obj:
            _extract_urls(it, out)
    return out


def _url_ext(url: str) -> str:
    path = str(url).split("?", 1)[0].lower()
    for ext in _AUDIO_EXTS + _VIDEO_EXTS + _IMAGE_EXTS:
        if path.endswith(ext):
            return ext
    return ""


def _suno_clip_audio_urls(data: dict[str, Any]) -> list[str]:
    """audioUrl из sunoData (длинные клипы первыми). Без обложек и stream."""
    resp = data.get("response") if isinstance(data.get("response"), dict) else data
    rows = resp.get("sunoData") if isinstance(resp, dict) else None
    if not isinstance(rows, list):
        return []
    ranked: list[tuple[float, str]] = []
    for it in rows:
        if not isinstance(it, dict):
            continue
        url = str(it.get("audioUrl") or it.get("sourceAudioUrl") or "").strip()
        if not url.startswith("http") or _url_ext(url) not in _AUDIO_EXTS:
            continue
        try:
            dur = float(it.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0.0
        ranked.append((dur, url))
    ranked.sort(key=lambda x: -x[0])
    seen: set[str] = set()
    out: list[str] = []
    for _, url in ranked:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _result_media_urls(data: dict[str, Any]) -> list[str]:
    """Ссылки на готовый файл: для Suno — mp3 из sunoData, иначе без картинок-обложек."""
    suno = _suno_clip_audio_urls(data)
    if suno:
        return suno
    urls = list(_extract_urls(data))
    raw = data.get("resultJson")
    if isinstance(raw, str) and raw.strip():
        with contextlib.suppress(json.JSONDecodeError):
            urls.extend(_extract_urls(json.loads(raw)))
    urls = list(dict.fromkeys(urls))
    audio = [u for u in urls if _url_ext(u) in _AUDIO_EXTS]
    if audio:
        return audio
    video = [u for u in urls if _url_ext(u) in _VIDEO_EXTS]
    if video:
        return video
    images = [u for u in urls if _url_ext(u) in _IMAGE_EXTS]
    return images or urls


def _task_state(api: str, data: dict[str, Any]) -> str:
    """Нормализованный статус: pending | success | fail."""
    if api == "jobs":
        state = str(data.get("state") or "").lower()
        if state == "success":
            return "success"
        if state == "fail":
            return "fail"
        return "pending"
    if api == "veo":
        flag = data.get("successFlag")
        try:
            flag_i = int(flag)
        except (TypeError, ValueError):
            flag_i = 0
        return {0: "pending", 1: "success", 2: "fail", 3: "fail"}.get(flag_i, "pending")
    # suno / runway: текстовый status
    st = str(data.get("status") or data.get("state") or "").upper()
    if st in ("SUCCESS", "FIRST_SUCCESS"):
        return "success"
    if st in ("FAILED", "FAIL", "CREATE_TASK_FAILED", "GENERATE_AUDIO_FAILED") or any(
            m in st for m in ("ERROR", "FAIL", "EXCEPTION")):
        return "fail"
    return "pending"


def _fail_message(data: dict[str, Any]) -> str:
    for key in ("failMsg", "failMessage", "errorMessage", "msg", "message"):
        v = str(data.get(key) or "").strip()
        if v:
            return v[:300]
    return "generation failed"


def _poll_backoff(fails: int) -> float:
    return min(_POLL_INTERVAL_S * (2 ** min(max(fails, 1) - 1, 3)), 30.0)


async def task_status(api: str, task_id: str) -> tuple[str, dict[str, Any]]:
    path = _POLL_PATHS.get(api, _POLL_PATHS["jobs"])
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.get(f"{kie_api_base_url()}{path}", headers=_headers(), params={"taskId": task_id})
    try:
        payload = r.json()
    except Exception:
        payload = {"msg": (r.text or "")[:300], "code": r.status_code}
    data = _check(payload, http_status=r.status_code, where=f"poll {path}")
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    return _task_state(api, inner), inner


@media_spend.tracked_kie_poll
async def poll_task(api: str, task_id: str, *, timeout_s: float = _POLL_MAX_S) -> dict[str, Any]:
    path = _POLL_PATHS.get(api, _POLL_PATHS["jobs"])
    url = f"{kie_api_base_url()}{path}"
    deadline = asyncio.get_running_loop().time() + max(60.0, timeout_s)
    net_fails = 0
    from app.services.media_transport import current_job, json_request
    from app.services.media_jobs import MediaPolicyError
    job = current_job.get()
    polls = 0
    interval = job.config["media"]["poll_interval_s"] if job else _POLL_INTERVAL_S
    if job:
        deadline = asyncio.get_running_loop().time() + min(timeout_s, job.config["media"]["timeout_s"])
    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            try:
                if job:
                    if polls >= job.config["media"]["max_polls"]:
                        raise MediaPolicyError("media status polling allowance exhausted")
                    r = await json_request("GET", path, params={"taskId": task_id}, headers=_headers())
                    polls += 1
                else:
                    r = await client.get(url, headers=_headers(), params={"taskId": task_id})
                net_fails = 0
                media_spend.count_poll()
            except (httpx.TransportError, httpx.TimeoutException) as e:
                if job:
                    raise
                net_fails += 1
                if asyncio.get_running_loop().time() >= deadline:
                    raise KieTaskPending(
                        f"kie poll {task_id}: сеть/таймаут ({type(e).__name__})",
                        task_id=task_id, kind="network",
                    ) from e
                await asyncio.sleep(_poll_backoff(net_fails))
                continue
            try:
                payload = r.json()
            except Exception:  # noqa: BLE001
                payload = {"msg": (r.text or "")[:300], "code": r.status_code}
            try:
                data = _check(payload, http_status=r.status_code, where=f"poll {path}")
            except KieHttpError as e:
                code = (e.context or {}).get("provider_code")
                if job or not (isinstance(code, int) and (code >= 500 or code in (429, 433, 455))):
                    raise
                net_fails += 1
                if asyncio.get_running_loop().time() >= deadline:
                    raise KieTaskPending(f"kie poll {task_id}: HTTP {code} до конца ожидания",
                                         task_id=task_id, kind="timeout") from e
                await asyncio.sleep(_poll_backoff(net_fails))
                continue
            net_fails = 0
            inner = data.get("data") if isinstance(data.get("data"), dict) else {}
            state = _task_state(api, inner)
            if job:
                raw_state = str(inner.get("state") or "").lower()
                phase = {"success": "completed", "fail": "failed", "cancelled": "cancelled",
                    "queued": "queued", "queuing": "queued", "waiting": "queued",
                    "pending": "queued", "generating": "running", "running": "running"}.get(raw_state, "unknown")
                await job.update(phase=phase)
                if phase in {"unknown", "cancelled"}:
                    raise MediaPolicyError("provider state " + phase)
            if (
                state == "success"
                and api == "suno"
                and str(inner.get("status") or "").upper() == "FIRST_SUCCESS"
                and not _result_media_urls(inner)
            ):
                state = "pending"
            if state == "success":
                return inner
            if state == "fail":
                raise KieHttpError(
                    f"kie task fail: {_fail_message(inner)}",
                    context={
                        "provider_code": 501,
                        "task_id": task_id,
                        "kind": "generation",
                    },
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise KieTaskPending(f"kie: таймаут ожидания task {task_id}", task_id=task_id, kind="timeout")
            await asyncio.sleep(interval)


async def download(url: str, out_path: Path) -> Path:
    from app.services.media_transport import current_job, download as managed_download
    if current_job.get() is not None:
        return await managed_download(current_job.get(), url)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        r = await client.get(url)
        if r.status_code >= 400 or len(r.content or b"") < 32:
            raise KieHttpError(
                f"kie download HTTP {r.status_code} size={len(r.content or b'')}",
                context={"provider_code": r.status_code, "kind": "download"},
            )
        tmp = out_path.with_name(out_path.name + ".part")
        tmp.write_bytes(r.content)
        tmp.replace(out_path)
    return out_path


async def _download_keep_task(url: str, out_path: Path, task_id: str) -> Path:
    last: BaseException | None = None
    for i in range(3):
        try:
            return await download(url, out_path)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            last = e
            if i < 2:
                await asyncio.sleep(2.0 * (i + 1))
    raise KieTaskPending(
        f"kie task {task_id}: результат готов, скачать не удалось ({type(last).__name__}); повторю скачивание без нового submit",
        task_id=task_id, kind="download", context={"raw_url": url},
    ) from last


async def run_generation(
    spec: dict[str, Any],
    payload: dict[str, Any],
    out_path: Path,
    *,
    timeout_s: float = _POLL_MAX_S,
    project_id: int | None = None,
) -> GenerationResult:
    from app.services.media_jobs import assert_entry
    if await assert_entry(provider="kie") is not None:
        return await _managed_generation(spec, payload, out_path, timeout_s=timeout_s)
    from app.services import media_resume
    api = str(spec.get("api") or "jobs")
    rctx = media_resume.make_ctx(project_id, out_path, str(spec.get("id") or ""), api, payload)
    with media_spend.kie_scope(spec, payload, out_path):
        resumed = await media_resume.begin(rctx)
        if resumed is not None:
            task_id = resumed
            logger.info("kie_http: возобновляю task {} без нового submit (model={})", task_id, spec.get("id"))
        else:
            task_id = await media_resume.submit(rctx, lambda: create_task(api, spec.get("endpoint"), payload))
            logger.info("kie_http: task {} создана (model={})", task_id, spec.get("id"))
        data = await poll_task(api, task_id, timeout_s=timeout_s)
    urls = _result_media_urls(data)
    if not urls:
        raise KieHttpError(
            "kie: success без ссылок на результат",
            context={"provider_code": 500, "task_id": task_id},
        )
    path = await _download_keep_task(urls[0], out_path, task_id)
    await media_resume.mark_downloaded(rctx, task_id)
    logger.info("kie_http: ok {} → {} ({} bytes)", task_id, path.name, path.stat().st_size)
    return GenerationResult(file_path=path, gen_id=task_id, raw_url=urls[0])


async def _managed_generation(spec, payload, out_path, *, timeout_s):
    from app.services.media_jobs import open_job, _hold, MediaPolicyError
    from app.services.media_transport import current_job, verify
    from app.services.observation import _attempt, record_fact
    if spec.get("api") != "jobs" or spec.get("result") not in {"image", "video", "audio"}:
        raise MediaPolicyError("provider family has no approved managed route")
    job = await open_job(provider="kie", kind=spec["result"], model_id=spec["id"],
                         payload=payload, out_path=out_path)
    token = current_job.set(job)
    try:
        if job.phase == "verified":
            await verify(job)
            return GenerationResult(file_path=job.path, gen_id=job.provider_job_id, raw_url=None)
        task_id = job.provider_job_id
        if not task_id:
            task_id = await create_task("jobs", spec.get("endpoint"), payload)
            await job.update(phase="queued", provider_job_id=task_id)
        data = await poll_task("jobs", task_id, timeout_s=timeout_s)
        urls = _result_media_urls(data)
        if not urls:
            raise MediaPolicyError("completed job has no artifact URL")
        path = await download(urls[0], job.path)
        await verify(job)
        return GenerationResult(file_path=path, gen_id=task_id, raw_url=urls[0])
    except asyncio.CancelledError:
        if job.provider_job_id:
            await record_fact("media_wait", {"job_id": job.job_id,
                "provider_job_id": job.provider_job_id, "reason": "local_wait_cancelled",
                "provider_cancelled": False, "cost_refunded": False})
        else:
            await job.update(phase="unknown", error="submit_interrupted")
            await _hold(_attempt.get(), "unknown media submit; no repeat", job.job_id)
        raise
    except Exception as exc:
        if job.phase not in {"verified", "failed", "cancelled"}:
            await job.update(phase="unknown",
                             error=type(exc).__name__)
        await record_fact("media_failure", {"job_id": job.job_id, "phase": job.phase,
            "error_type": type(exc).__name__, "provider_job_id": job.provider_job_id,
            "uncertain": not bool(job.provider_job_id), "protective_stop": isinstance(exc, MediaPolicyError)})
        await _hold(_attempt.get(), "media result requires review: " + type(exc).__name__, job.job_id)
        raise
    finally:
        current_job.reset(token)


VIDEO_GENERATOR_TO_KIE_ID: dict[str, str] = {
    "seedance_2_5": "seedance-2-5",
    "seedance-2-5": "seedance-2-5",
    "seedance_1_5_pro": "seedance-1-5-pro",
    "seedance-1-5-pro": "seedance-1-5-pro",
    "kling_3_0": "kling-3-0",
    "kling-3-0": "kling-3-0",
    "kling_3": "kling-3-0",
    "kling_v3_turbo_t2v": "kling-v3-turbo-t2v",
    "kling-v3-turbo-t2v": "kling-v3-turbo-t2v",
    "kling_v3_turbo_i2v": "kling-v3-turbo-i2v",
    "kling-v3-turbo-i2v": "kling-v3-turbo-i2v",
    "kling_3_0_omni_t2v": "kling-3-0-omni-t2v",
    "kling-3-0-omni-t2v": "kling-3-0-omni-t2v",
    "hailuo_2_3_i2v": "hailuo-2-3-i2v",
    "hailuo-2-3-i2v": "hailuo-2-3-i2v",
    "wan_2_7_t2v": "wan-2-7-t2v",
    "wan-2-7-t2v": "wan-2-7-t2v",
    "pixverse_v6_t2v": "pixverse-v6-t2v",
    "pixverse-v6-t2v": "pixverse-v6-t2v",
    "topaz_video_upscale": "topaz-video-upscale",
    "topaz-video-upscale": "topaz-video-upscale",
    "kling_2_6": "kling-2-6",
    "kling-2-6": "kling-2-6",
}

# Маппинг генераторов картинок на ID модели в kie_catalog
IMAGE_GENERATOR_TO_KIE_ID: dict[str, str] = {
    "flux_2_pro": "flux-2-pro",
    "flux-2-pro": "flux-2-pro",
    "seedream_5_pro": "seedream-5-pro",
    "seedream-5-pro": "seedream-5-pro",
    "z_image": "z-image",
    "z-image": "z-image",
    "qwen3_image": "qwen3-image",
    "qwen3-image": "qwen3-image",
    "topaz_image_upscale": "topaz-image-upscale",
    "topaz-image-upscale": "topaz-image-upscale",
    "recraft_remove_bg": "recraft-remove-bg",
    "recraft-remove-bg": "recraft-remove-bg",
    "recraft_crisp_upscale": "recraft-crisp-upscale",
    "recraft-crisp-upscale": "recraft-crisp-upscale",
}


async def generate_kie_video(
    prompt: str,
    out_path: Path,
    *,
    model_slug: str,
    start_frame: Path | str | None = None,
    aspect_ratio: str | None = "9:16",
    resolution: str | None = "720p",
    duration: int | float | None = 5,
    generate_audio: bool = False,
    timeout: float = 900.0,
    project_id: int | None = None,
    gen_id: str | None = None,
) -> GenerationResult:
    """Генерация видео через KIE для любой из поддерживаемых KIE-моделей."""
    from app.bots.kie_kling import _frame_to_public_url
    from app.services import kie_catalog
    from app.services.media_jobs import assert_entry, MediaPolicyError

    managed = await assert_entry(project_id=project_id, provider="kie")
    if not managed and not kie_configured():
        raise KieHttpError(
            f"kie {model_slug}: API не настроен (KIE_API_KEY / GPT_API_KEY)",
            context={"provider_code": 401, "error_kind": "no_key"},
        )

    kie_id = VIDEO_GENERATOR_TO_KIE_ID.get(model_slug) or model_slug.replace("_", "-")
    spec = kie_catalog.get_model(kie_id)
    if not spec:
        if managed:
            raise MediaPolicyError("managed video requires an explicit catalog model; no fallback")
        from app.bots.kie_kling import generate_video as kie_kling_generate_video

        return await media_spend.track_kling(
            kie_kling_generate_video(
                prompt,
                out_path,
                start_frame=start_frame,
                aspect_ratio=aspect_ratio,
                duration=duration,
                generate_audio=generate_audio,
                timeout=timeout,
                project_id=project_id,
                gen_id=gen_id,
            ),
            kie_id=kie_id, duration=duration, has_frame=start_frame is not None,
        )

    field_names = {f["name"] for f in spec.get("fields") or []}
    image_fields = {"first_frame_url", "image_url", "image_urls", "imageUrls", "input_image_url"}
    if managed and start_frame and field_names & image_fields:
        raise MediaPolicyError("managed reference upload has no authorized adapter")
    image_url = None if managed else await _frame_to_public_url(start_frame)
    ar = (aspect_ratio or "9:16").replace("_", ":")
    if "seedance" in str(kie_id).lower() and image_url:
        ar = "adaptive"

    values: dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": ar,
        "duration": int(duration) if duration else 5,
        "generate_audio": bool(generate_audio),
    }
    if resolution:
        values["resolution"] = resolution.lower()

    if image_url:
        field_names = {f["name"] for f in spec.get("fields") or []}
        if "first_frame_url" in field_names:
            values["first_frame_url"] = image_url
        elif "image_url" in field_names:
            values["image_url"] = image_url
        elif "image_urls" in field_names:
            values["image_urls"] = [image_url]
        elif "imageUrls" in field_names:
            values["imageUrls"] = [image_url]
        elif "input_image_url" in field_names:
            values["input_image_url"] = image_url

    payload = kie_catalog.build_payload(spec, values)
    logger.info(
        "kie_http: generate video {} (model={}) dur={} i2v={} project={}",
        kie_id,
        spec.get("model"),
        values.get("duration"),
        bool(image_url),
        project_id,
    )
    return await run_generation(spec, payload, out_path, timeout_s=timeout, project_id=project_id)


async def generate_kie_image(
    prompt: str,
    out_path: Path,
    *,
    model_slug: str,
    aspect_ratio: str | None = "9:16",
    resolution: str | None = "2K",
    reference_images: list[Path] | None = None,
    timeout: float = 600.0,
    project_id: int | None = None,
    gen_id: str | None = None,
) -> GenerationResult:
    """Генерация изображения через KIE."""
    from app.bots.kie_kling import _frame_to_public_url
    from app.services import kie_catalog
    from app.services.media_jobs import assert_entry, MediaPolicyError

    managed = await assert_entry(project_id=project_id, provider="kie")
    if managed and reference_images:
        raise MediaPolicyError("managed reference upload has no authorized adapter")
    if not managed and not kie_configured():
        raise KieHttpError(
            f"kie {model_slug}: API не настроен (KIE_API_KEY / GPT_API_KEY)",
            context={"provider_code": 401, "error_kind": "no_key"},
        )

    kie_id = IMAGE_GENERATOR_TO_KIE_ID.get(model_slug) or model_slug.replace("_", "-")
    spec = kie_catalog.get_model(kie_id)
    if not spec:
        raise KieHttpError(
            f"kie {model_slug}: спецификация модели {kie_id} не найдена в каталоге",
            context={"provider_code": 404, "error_kind": "model_not_found"},
        )

    ref_urls: list[str] = []
    if reference_images:
        for ref in reference_images:
            u = await _frame_to_public_url(ref)
            if u:
                ref_urls.append(u)

    values: dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": (aspect_ratio or "9:16").replace("_", ":"),
    }
    if resolution:
        values["resolution"] = resolution.upper() if resolution.lower().endswith("k") else resolution

    if ref_urls:
        field_names = {f["name"] for f in spec.get("fields") or []}
        if "image_urls" in field_names:
            values["image_urls"] = ref_urls
        elif "image_url" in field_names:
            values["image_url"] = ref_urls[0]
        elif "imageUrls" in field_names:
            values["imageUrls"] = ref_urls

    payload = kie_catalog.build_payload(spec, values)
    logger.info(
        "kie_http: generate image {} (model={}) aspect={} refs={} project={}",
        kie_id,
        spec.get("model"),
        values.get("aspect_ratio"),
        len(ref_urls),
        project_id,
    )
    return await run_generation(spec, payload, out_path, timeout_s=timeout, project_id=project_id)

