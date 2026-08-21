"""Kie.ai Kling 2.6 video client (Market jobs API).

Docs:
  https://docs.kie.ai/market/kling/image-to-video
  https://docs.kie.ai/market/kling/text-to-video
  https://docs.kie.ai/market/common/get-task-detail

Models:
  - kling-2.6/image-to-video  (prompt, image_urls[1], sound, duration 5|10)
  - kling-2.6/text-to-video   (prompt, sound, aspect_ratio, duration 5|10)

Auth: Bearer API key (KIE_API_KEY или GPT_API_KEY при базе kie.ai).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from loguru import logger

from app.bots.outsee import GenerationResult, OutseeImageError
from app.generation_options import KIE_KLING_PROMPT_MAX_CHARS
from app.settings import settings

KLING_I2V_MODEL = "kling-2.6/image-to-video"
KLING_T2V_MODEL = "kling-2.6/text-to-video"
_CREATE_PATH = "/api/v1/jobs/createTask"
_DETAIL_PATH = "/api/v1/jobs/recordInfo"
_DEFAULT_BASE = "https://api.kie.ai"
_POLL_INTERVAL_S = 4.0
_POLL_MAX_S = 900.0


class KieKlingError(OutseeImageError):
    """Ошибка Kling 2.6 / kie Market — совместима с outsee_retry."""


def kie_api_configured() -> bool:
    return bool(kie_api_key() and kie_api_base_url())


def kie_api_key() -> str:
    key = (getattr(settings, "kie_api_key", None) or "").strip()
    if key:
        return key
    # Тот же ключ, что для GPT на kie.ai
    base = (settings.gpt_base_url or "").strip().lower()
    if "kie.ai" in base:
        return (settings.gpt_api_key or "").strip()
    return (settings.gpt_api_key or "").strip()


def kie_api_base_url() -> str:
    base = (getattr(settings, "kie_api_base_url", None) or "").strip()
    if base:
        return base.rstrip("/")
    gpt_base = (settings.gpt_base_url or "").strip().rstrip("/")
    if "kie.ai" in gpt_base.lower():
        # GPT_BASE_URL может быть https://api.kie.ai — ок для jobs
        return gpt_base.split("/codex")[0].rstrip("/") or _DEFAULT_BASE
    return _DEFAULT_BASE


def truncate_kling_prompt(prompt: str, *, max_chars: int = KIE_KLING_PROMPT_MAX_CHARS) -> str:
    text = (prompt or "").strip()
    if len(text) <= max_chars:
        return text
    cut = text[: max_chars - 1].rstrip()
    # не рвать посередине слова, если можно
    sp = cut.rfind(" ")
    if sp > max_chars // 2:
        cut = cut[:sp]
    return cut


def map_kling_duration(seconds: int | float | None) -> str:
    """Kling 2.6 принимает только '5' | '10'."""
    if seconds is None:
        return "5"
    try:
        n = float(seconds)
    except (TypeError, ValueError):
        return "5"
    return "10" if n > 7 else "5"


def map_kling_aspect(aspect: str | None) -> str:
    s = (aspect or "9:16").replace("_", ":").strip()
    if s in ("1:1", "16:9", "9:16"):
        return s
    # «Исходное» / другие — вертикаль пайплайна
    return "9:16"


def _auth_headers() -> dict[str, str]:
    key = kie_api_key()
    if not key:
        raise KieKlingError(
            "kie Kling: нет API ключа (KIE_API_KEY / GPT_API_KEY)",
            context={"provider_code": 401, "error_kind": "no_key"},
        )
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _raise_from_kie_payload(
    payload: dict[str, Any] | None,
    *,
    http_status: int | None = None,
    where: str,
) -> None:
    data = payload if isinstance(payload, dict) else {}
    code = data.get("code", http_status)
    msg = str(data.get("msg") or data.get("message") or where)
    try:
        code_i = int(code) if code is not None else http_status
    except (TypeError, ValueError):
        code_i = http_status
    if code_i in (None, 200, 0):
        return
    raise KieKlingError(
        f"kie Kling {where}: code={code_i} {msg}"[:400],
        context={"provider_code": code_i, "kie_msg": msg[:200]},
    )


async def _create_task(body: dict[str, Any]) -> str:
    url = f"{kie_api_base_url()}{_CREATE_PATH}"
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(url, headers=_auth_headers(), json=body)
        try:
            payload = r.json()
        except Exception:  # noqa: BLE001
            payload = {"msg": (r.text or "")[:300], "code": r.status_code}
        if r.status_code >= 400:
            _raise_from_kie_payload(payload, http_status=r.status_code, where="createTask")
        _raise_from_kie_payload(payload, http_status=r.status_code, where="createTask")
        data = (payload or {}).get("data") if isinstance(payload, dict) else None
        task_id = ""
        if isinstance(data, dict):
            task_id = str(data.get("taskId") or data.get("task_id") or "").strip()
        if not task_id:
            raise KieKlingError(
                "kie Kling: createTask без taskId",
                context={"provider_code": 500, "raw": str(payload)[:200]},
            )
        return task_id


async def _poll_task(task_id: str, *, timeout_s: float) -> dict[str, Any]:
    url = f"{kie_api_base_url()}{_DETAIL_PATH}"
    deadline = asyncio.get_running_loop().time() + max(30.0, timeout_s)
    net_fail_streak = 0
    async with httpx.AsyncClient(timeout=60.0) as client:
        while True:
            try:
                r = await client.get(
                    url,
                    headers=_auth_headers(),
                    params={"taskId": task_id},
                )
                net_fail_streak = 0
            except (httpx.TransportError, httpx.TimeoutException) as e:
                net_fail_streak += 1
                logger.warning(
                    "kie_kling: poll net error {}/8 taskId={} ({})",
                    net_fail_streak,
                    task_id,
                    type(e).__name__,
                )
                if net_fail_streak >= 8:
                    raise KieKlingError(
                        f"kie Kling: сеть при poll task {task_id}: {type(e).__name__}",
                        context={
                            "provider_code": 503,
                            "task_id": task_id,
                            "kind": "network",
                        },
                    ) from e
                if asyncio.get_running_loop().time() >= deadline:
                    raise KieKlingError(
                        f"kie Kling: таймаут ожидания task {task_id}",
                        context={
                            "provider_code": 504,
                            "task_id": task_id,
                            "kind": "timeout",
                        },
                    ) from e
                await asyncio.sleep(_POLL_INTERVAL_S)
                continue
            if r.status_code in {500, 502, 503, 504}:
                net_fail_streak += 1
                logger.warning(
                    "kie_kling: poll transient HTTP {} {}/8 taskId={}",
                    r.status_code,
                    net_fail_streak,
                    task_id,
                )
                if net_fail_streak < 8:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise KieKlingError(
                            f"kie Kling: таймаут ожидания task {task_id}",
                            context={"provider_code": 504, "task_id": task_id, "kind": "timeout"},
                        )
                    await asyncio.sleep(_POLL_INTERVAL_S * 1.5)
                    continue
            try:
                payload = r.json()
            except Exception:  # noqa: BLE001
                payload = {"msg": (r.text or "")[:300], "code": r.status_code}
            if r.status_code >= 400:
                _raise_from_kie_payload(payload, http_status=r.status_code, where="recordInfo")
            _raise_from_kie_payload(payload, http_status=r.status_code, where="recordInfo")
            data = (payload or {}).get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                raise KieKlingError(
                    "kie Kling: recordInfo без data",
                    context={"provider_code": 500, "task_id": task_id},
                )
            state = str(data.get("state") or "").lower()
            if state == "success":
                return data
            if state == "fail":
                fail_code = str(data.get("failCode") or "")
                fail_msg = str(data.get("failMsg") or "generation failed")
                try:
                    pc = int(fail_code) if fail_code.strip().isdigit() else 501
                except ValueError:
                    pc = 501
                raise KieKlingError(
                    f"kie Kling task fail: {fail_msg}"[:400],
                    context={
                        "provider_code": pc,
                        "task_id": task_id,
                        "fail_code": fail_code,
                        "kind": "generation",
                    },
                )
            if asyncio.get_running_loop().time() >= deadline:
                raise KieKlingError(
                    f"kie Kling: таймаут ожидания task {task_id}",
                    context={"provider_code": 504, "task_id": task_id, "kind": "timeout"},
                )
            await asyncio.sleep(_POLL_INTERVAL_S)


def _result_url_from_task(data: dict[str, Any]) -> str:
    raw = data.get("resultJson")
    parsed: Any
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
    elif isinstance(raw, dict):
        parsed = raw
    else:
        parsed = None
    if isinstance(parsed, dict):
        urls = parsed.get("resultUrls") or parsed.get("result_urls") or []
        if isinstance(urls, list) and urls:
            u = str(urls[0] or "").strip()
            if u.startswith("http"):
                return u
    # иногда URL лежит плоско
    for key in ("resultUrl", "videoUrl", "url"):
        u = str(data.get(key) or "").strip()
        if u.startswith("http"):
            return u
    raise KieKlingError(
        "kie Kling: success без resultUrls",
        context={"provider_code": 500, "task_id": str(data.get("taskId") or "")},
    )


async def _download(url: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=180.0, follow_redirects=True) as client:
        r = await client.get(url)
        if r.status_code >= 400 or len(r.content or b"") < 1024:
            raise KieKlingError(
                f"kie Kling download HTTP {r.status_code} size={len(r.content or b'')}",
                context={"provider_code": r.status_code, "video_url": url, "kind": "download"},
            )
        out_path.write_bytes(r.content)
    return out_path


async def _frame_to_public_url(start_frame: Path | str | None) -> str | None:
    if start_frame is None:
        return None
    from app.bots.outsee_http import _path_to_data_url, ensure_public_image_url

    if isinstance(start_frame, Path):
        if not start_frame.is_file():
            return None
        data_url = _path_to_data_url(start_frame)
        return await ensure_public_image_url(data_url)
    s = str(start_frame).strip()
    if not s:
        return None
    if s.startswith(("http://", "https://", "data:")):
        return await ensure_public_image_url(s)
    p = Path(s)
    if p.is_file():
        return await ensure_public_image_url(_path_to_data_url(p))
    return None


async def generate_video(
    prompt: str,
    out_path: Path,
    *,
    start_frame: Path | str | None = None,
    aspect_ratio: str | None = "9:16",
    duration: int | float | None = 5,
    generate_audio: bool = False,
    timeout: float = _POLL_MAX_S,
    project_id: int | None = None,
    gen_id: str | None = None,
    **_ignored: Any,
) -> GenerationResult:
    """Сгенерировать клип Kling 2.6. i2v если есть кадр, иначе t2v."""
    if not kie_api_configured():
        raise KieKlingError(
            "kie Kling: API не настроен",
            context={"provider_code": 401, "error_kind": "no_key"},
        )
    body_prompt = truncate_kling_prompt(prompt)
    dur = map_kling_duration(duration)
    sound = bool(generate_audio)
    image_url = await _frame_to_public_url(start_frame)
    gid = gen_id or uuid4().hex[:12]

    # Пайплайн всегда передаёт start_frame: без публичного URL нельзя уходить в t2v
    # (молча «успех без картинки» = вечный баг «не приложилось»).
    if start_frame is not None and not image_url:
        raise KieKlingError(
            "kie Kling: start_frame передан, но public image_url не получен (host/upload)",
            context={
                "provider_code": 502,
                "error_kind": "frame_host",
                "project_id": project_id,
            },
        )

    if image_url:
        model = KLING_I2V_MODEL
        body = {
            "model": model,
            "input": {
                "prompt": body_prompt,
                "image_urls": [image_url],
                "sound": sound,
                "duration": dur,
            },
        }
    else:
        model = KLING_T2V_MODEL
        body = {
            "model": model,
            "input": {
                "prompt": body_prompt,
                "sound": sound,
                "aspect_ratio": map_kling_aspect(aspect_ratio),
                "duration": dur,
            },
        }

    logger.info(
        "kie_kling: createTask model={} dur={} sound={} i2v={} prompt_chars={} project={}",
        model,
        dur,
        sound,
        bool(image_url),
        len(body_prompt),
        project_id,
    )
    task_id = await _create_task(body)
    logger.info("kie_kling: taskId={} gen_id={}", task_id, gid)
    data = await _poll_task(task_id, timeout_s=float(timeout or _POLL_MAX_S))
    raw_url = _result_url_from_task(data)
    path = await _download(raw_url, out_path)
    logger.info("kie_kling: ok {} → {} ({} bytes)", task_id, path.name, path.stat().st_size)
    return GenerationResult(file_path=path, gen_id=gid, raw_url=raw_url)
