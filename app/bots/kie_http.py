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


async def create_task(api: str, endpoint: str | None, body: dict[str, Any]) -> str:
    """Создать задачу; возвращает taskId."""
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
    r, payload, _src = await kie_post_json(url, body, timeout=120.0)
    data = _check(payload, http_status=r.status_code, where=f"create {path}")
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    task_id = str(
        inner.get("taskId") or inner.get("task_id") or inner.get("id") or ""
    ).strip()
    if not task_id:
        raise KieHttpError(
            f"kie create {path}: ответ без taskId",
            context={"provider_code": 500, "raw": str(payload)[:200]},
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
    if st in ("FAILED", "FAIL", "CREATE_TASK_FAILED", "GENERATE_AUDIO_FAILED"):
        return "fail"
    return "pending"


def _fail_message(data: dict[str, Any]) -> str:
    for key in ("failMsg", "failMessage", "errorMessage", "msg", "message"):
        v = str(data.get(key) or "").strip()
        if v:
            return v[:300]
    return "generation failed"


async def poll_task(api: str, task_id: str, *, timeout_s: float = _POLL_MAX_S) -> dict[str, Any]:
    path = _POLL_PATHS.get(api, _POLL_PATHS["jobs"])
    url = f"{kie_api_base_url()}{path}"
    deadline = asyncio.get_event_loop().time() + max(60.0, timeout_s)
    net_fails = 0
    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            try:
                r = await client.get(
                    url, headers=_headers(), params={"taskId": task_id}
                )
                net_fails = 0
            except (httpx.TransportError, httpx.TimeoutException) as e:
                net_fails += 1
                if net_fails >= 8 or asyncio.get_event_loop().time() >= deadline:
                    raise KieHttpError(
                        f"kie poll {task_id}: сеть/таймаут ({type(e).__name__})",
                        context={"provider_code": 504, "task_id": task_id},
                    ) from e
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue
            try:
                payload = r.json()
            except Exception:  # noqa: BLE001
                payload = {"msg": (r.text or "")[:300], "code": r.status_code}
            data = _check(payload, http_status=r.status_code, where=f"poll {path}")
            inner = data.get("data") if isinstance(data.get("data"), dict) else {}
            state = _task_state(api, inner)
            # FIRST_SUCCESS у Suno часто без audioUrl — ждём SUCCESS, иначе качаем jpeg/stream.
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
            if asyncio.get_event_loop().time() >= deadline:
                raise KieHttpError(
                    f"kie: таймаут ожидания task {task_id}",
                    context={"provider_code": 504, "task_id": task_id, "kind": "timeout"},
                )
            await asyncio.sleep(_POLL_INTERVAL_S)


async def download(url: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
        r = await client.get(url)
        if r.status_code >= 400 or len(r.content or b"") < 32:
            raise KieHttpError(
                f"kie download HTTP {r.status_code} size={len(r.content or b'')}",
                context={"provider_code": r.status_code, "kind": "download"},
            )
        out_path.write_bytes(r.content)
    return out_path


async def run_generation(
    spec: dict[str, Any],
    payload: dict[str, Any],
    out_path: Path,
    *,
    timeout_s: float = _POLL_MAX_S,
) -> GenerationResult:
    """create → poll → download. spec.api выбирает семейство эндпоинтов."""
    api = str(spec.get("api") or "jobs")
    task_id = await create_task(api, spec.get("endpoint"), payload)
    logger.info("kie_http: task {} создана (model={})", task_id, spec.get("id"))
    data = await poll_task(api, task_id, timeout_s=timeout_s)
    urls = _result_media_urls(data)
    if not urls:
        raise KieHttpError(
            "kie: success без ссылок на результат",
            context={"provider_code": 500, "task_id": task_id},
        )
    path = await download(urls[0], out_path)
    logger.info("kie_http: ok {} → {} ({} bytes)", task_id, path.name, path.stat().st_size)
    return GenerationResult(file_path=path, gen_id=task_id, raw_url=urls[0])


# Маппинг генераторов видео на ID модели в kie_catalog
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

    if not kie_configured():
        raise KieHttpError(
            f"kie {model_slug}: API не настроен (KIE_API_KEY / GPT_API_KEY)",
            context={"provider_code": 401, "error_kind": "no_key"},
        )

    kie_id = VIDEO_GENERATOR_TO_KIE_ID.get(model_slug) or model_slug.replace("_", "-")
    spec = kie_catalog.get_model(kie_id)
    if not spec:
        from app.bots.kie_kling import generate_video as kie_kling_generate_video

        return await kie_kling_generate_video(
            prompt,
            out_path,
            start_frame=start_frame,
            aspect_ratio=aspect_ratio,
            duration=duration,
            generate_audio=generate_audio,
            timeout=timeout,
            project_id=project_id,
            gen_id=gen_id,
        )

    image_url = await _frame_to_public_url(start_frame)
    values: dict[str, Any] = {
        "prompt": prompt,
        "aspect_ratio": (aspect_ratio or "9:16").replace("_", ":"),
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
    return await run_generation(spec, payload, out_path, timeout_s=timeout)


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

    if not kie_configured():
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
    return await run_generation(spec, payload, out_path, timeout_s=timeout)

