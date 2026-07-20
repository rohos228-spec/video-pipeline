"""Клиент Grsai API (https://grsaiapi.com) — картинки без CDP/outsee.

Документация: POST /v1/api/generate (+ replyType=json|async),
GET /v1/api/result?id=…
Модели: gpt-image-2, nano-banana-2, nano-banana-pro, …
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from app.bots.outsee import GenerationResult, OutseeImageError
from app.settings import settings

# Временно подключённые модели (UI помечает «+»).
GRSAI_WIRED_IMAGE_MODELS: tuple[str, ...] = (
    "gpt-image-2",
    "gpt-image-2-vip",
    "nano-banana-2",
    "nano-banana-2-lite",
    "nano-banana-pro",
    "nano-banana-fast",
    "nano-banana",
    "nano-banana-pro-vt",
    "nano-banana-pro-cl",
    "nano-banana-2-cl",
    "nano-banana-2-2k-cl",
    "nano-banana-2-4k-cl",
    "nano-banana-pro-vip",
    "nano-banana-pro-4k-vip",
)

# gpt-image-2: aspectRatio как "9:16" или пиксели; banana: aspectRatio + imageSize
_GPT_IMAGE_FAMILY = frozenset({"gpt-image-2", "gpt-image-2-vip"})


@dataclass
class GrsaiModelInfo:
    slug: str
    display_name: str
    wired: bool
    family: str  # gpt-image | nano-banana
    resolutions: tuple[str, ...]
    aspects: tuple[str, ...]


GRSAI_IMAGE_CATALOG: list[GrsaiModelInfo] = [
    GrsaiModelInfo(
        "gpt-image-2", "GPT Image 2", True, "gpt-image",
        ("1K",), ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"),
    ),
    GrsaiModelInfo(
        "gpt-image-2-vip", "GPT Image 2 VIP", True, "gpt-image",
        ("1K", "2K", "4K"),
        ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"),
    ),
    GrsaiModelInfo(
        "nano-banana-2", "Nano Banana 2", True, "nano-banana",
        ("1K", "2K", "4K"),
        ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"),
    ),
    GrsaiModelInfo(
        "nano-banana-2-lite", "Nano Banana 2 Lite", True, "nano-banana",
        ("1K", "2K"),
        ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"),
    ),
    GrsaiModelInfo(
        "nano-banana-pro", "Nano Banana Pro", True, "nano-banana",
        ("1K", "2K", "4K"),
        ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"),
    ),
    GrsaiModelInfo(
        "nano-banana-fast", "Nano Banana Fast", True, "nano-banana",
        ("1K", "2K"),
        ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"),
    ),
    GrsaiModelInfo(
        "nano-banana", "Nano Banana", True, "nano-banana",
        ("1K", "2K"),
        ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"),
    ),
    GrsaiModelInfo(
        "nano-banana-pro-vt", "Nano Banana Pro VT", True, "nano-banana",
        ("1K", "2K", "4K"),
        ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "5:4", "4:5", "21:9"),
    ),
]


class GrsaiError(OutseeImageError):
    """Ошибка Grsai — совместима с outsee retry (isinstance OutseeImageError)."""


def grsai_enabled() -> bool:
    return bool((settings.grsai_api_key or "").strip()) and (
        (settings.image_provider or "grsai").lower() == "grsai"
    )


def _base_url() -> str:
    return (settings.grsai_base_url or "https://grsaiapi.com").rstrip("/")


def _headers() -> dict[str, str]:
    key = (settings.grsai_api_key or "").strip()
    if not key:
        raise GrsaiError("GRSAI_API_KEY пуст — задай в .env")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }


def _normalize_aspect(aspect: str | None) -> str:
    a = (aspect or "9:16").strip().replace("_", ":")
    if "x" in a.lower() and ":" not in a:
        return a  # pixel size like 1024x1024
    return a


def _normalize_size(resolution: str | None) -> str:
    r = (resolution or "1K").strip().upper()
    if r in {"1K", "2K", "3K", "4K"}:
        return r
    return "1K"


def _file_to_data_url(path: Path) -> str:
    raw = path.read_bytes()
    mime = "image/png"
    suf = path.suffix.lower()
    if suf in {".jpg", ".jpeg"}:
        mime = "image/jpeg"
    elif suf == ".webp":
        mime = "image/webp"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_generate_body(
    *,
    model: str,
    prompt: str,
    aspect_ratio: str = "9:16",
    resolution: str | None = "1K",
    reference_images: list[Path] | None = None,
    reply_type: str = "json",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "replyType": reply_type,
    }
    refs = list(reference_images or [])
    if refs:
        body["images"] = [_file_to_data_url(p) for p in refs if p.is_file()]

    aspect = _normalize_aspect(aspect_ratio)
    size = _normalize_size(resolution)

    if model in _GPT_IMAGE_FAMILY:
        # gpt-image-2: aspectRatio = ratio или пиксели; vip — лучше пиксели 1-4K
        if model == "gpt-image-2-vip":
            # map size+aspect to approximate pixels from docs
            body["aspectRatio"] = _vip_pixel_size(aspect, size)
        else:
            body["aspectRatio"] = aspect
    else:
        body["aspectRatio"] = aspect
        body["imageSize"] = size
    return body


def _vip_pixel_size(aspect: str, size: str) -> str:
    """Грубый маппинг aspect+K → пиксели для gpt-image-2-vip."""
    table = {
        ("1:1", "1K"): "1024x1024",
        ("1:1", "2K"): "2048x2048",
        ("1:1", "4K"): "2880x2880",
        ("16:9", "1K"): "1280x720",
        ("16:9", "2K"): "2048x1152",
        ("16:9", "4K"): "3840x2160",
        ("9:16", "1K"): "720x1280",
        ("9:16", "2K"): "1152x2048",
        ("9:16", "4K"): "2160x3840",
        ("4:3", "1K"): "1152x864",
        ("4:3", "2K"): "2304x1728",
        ("3:4", "1K"): "864x1152",
        ("3:4", "2K"): "1728x2304",
    }
    return table.get((aspect, size), table.get((aspect, "1K"), "1024x1024"))


async def _download(url: str, out_path: Path, *, timeout: float = 120) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        out_path.write_bytes(r.content)


def _extract_result_url(payload: dict[str, Any]) -> str | None:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None
    results = data.get("results") or payload.get("results")
    if isinstance(results, list) and results:
        first = results[0]
        if isinstance(first, dict) and first.get("url"):
            return str(first["url"])
        if isinstance(first, str) and first.startswith("http"):
            return first
    if data.get("url"):
        return str(data["url"])
    return None


def _status_of(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if isinstance(data, dict):
        return str(data.get("status") or payload.get("status") or "")
    return str(payload.get("status") or "")


async def generate_image(
    prompt: str,
    out_path: Path,
    *,
    model_slug: str | None = None,
    aspect_ratio: str = "9:16",
    resolution: str | None = "1K",
    reference_image: Path | list[Path] | None = None,
    timeout: float = 600,
    gen_id: str | None = None,
    project_id: int | None = None,
    **_kwargs: Any,
) -> GenerationResult:
    """Сгенерировать картинку через Grsai и сохранить в out_path."""
    model = (model_slug or settings.grsai_default_image_model or "gpt-image-2").strip()
    refs: list[Path] = []
    if isinstance(reference_image, Path):
        refs = [reference_image]
    elif isinstance(reference_image, list):
        refs = list(reference_image)

    body = build_generate_body(
        model=model,
        prompt=prompt,
        aspect_ratio=aspect_ratio,
        resolution=resolution,
        reference_images=refs,
        reply_type="json",
    )
    url = f"{_base_url()}/v1/api/generate"
    logger.info(
        "grsai.generate_image model={} aspect={} size={} project={} out={}",
        model,
        body.get("aspectRatio"),
        body.get("imageSize"),
        project_id,
        out_path.name,
    )

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, headers=_headers(), json=body)
            if resp.status_code >= 400:
                raise GrsaiError(
                    f"grsai HTTP {resp.status_code}: {resp.text[:400]}",
                    context={"status_code": resp.status_code, "model": model},
                )
            payload = resp.json()
    except httpx.TimeoutException as e:
        raise GrsaiError(
            f"grsai timeout {timeout:.0f}s model={model}",
            context={"model": model, "error_kind": "timeout"},
        ) from e
    except GrsaiError:
        raise
    except Exception as e:  # noqa: BLE001
        raise GrsaiError(
            f"grsai request failed: {type(e).__name__}: {e}",
            context={"model": model},
        ) from e

    status = _status_of(payload)
    if status == "running":
        task_id = str(payload.get("id") or "")
        if not task_id:
            raise GrsaiError("grsai: async без id", context={"payload": payload})
        payload = await _poll_result(task_id, timeout=timeout)
        status = _status_of(payload)

    if status == "violation":
        raise GrsaiError(
            f"grsai moderation: {payload.get('error') or 'violation'}",
            context={"model": model, "error_kind": "moderation", "payload": payload},
        )
    if status != "succeeded":
        err = payload.get("error") or (payload.get("data") or {}).get("error") if isinstance(payload.get("data"), dict) else None
        raise GrsaiError(
            f"grsai failed status={status}: {err or payload}",
            context={"model": model, "status": status},
        )

    result_url = _extract_result_url(payload)
    if not result_url:
        raise GrsaiError(
            "grsai succeeded без url",
            context={"model": model, "payload": payload},
        )

    try:
        await _download(result_url, out_path)
    except Exception as e:  # noqa: BLE001
        raise GrsaiError(
            f"grsai download failed: {e}",
            context={"url": result_url, "model": model},
        ) from e

    if not out_path.is_file() or out_path.stat().st_size < 32:
        raise GrsaiError("grsai: пустой файл после download", context={"path": str(out_path)})

    logger.info(
        "grsai.generate_image OK model={} bytes={} gen_id={}",
        model,
        out_path.stat().st_size,
        gen_id,
    )
    return GenerationResult(file_path=out_path, raw_url=result_url, gen_id=gen_id)


async def _poll_result(task_id: str, *, timeout: float = 600) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    url = f"{_base_url()}/v1/api/result"
    last: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=30) as client:
        while asyncio.get_event_loop().time() < deadline:
            r = await client.get(url, headers=_headers(), params={"id": task_id})
            if r.status_code >= 400:
                raise GrsaiError(
                    f"grsai poll HTTP {r.status_code}: {r.text[:300]}",
                    context={"task_id": task_id},
                )
            last = r.json()
            st = _status_of(last)
            if st in {"succeeded", "failed", "violation"}:
                return last
            await asyncio.sleep(3.0)
    raise GrsaiError(
        f"grsai poll timeout task={task_id}",
        context={"task_id": task_id, "last": last},
    )


def studio_id_to_grsai_slug(studio_id: str | None) -> str:
    """Project.image_generator → grsai model slug."""
    if not studio_id:
        return settings.grsai_default_image_model or "gpt-image-2"
    # already a slug?
    if studio_id in {m.slug for m in GRSAI_IMAGE_CATALOG}:
        return studio_id
    mapping = {
        "gpt_image_2": "gpt-image-2",
        "gpt_image_2_vip": "gpt-image-2-vip",
        "nano_banana_2": "nano-banana-2",
        "nano_banana_2_lite": "nano-banana-2-lite",
        "nano_banana_pro": "nano-banana-pro",
        "nano_banana_fast": "nano-banana-fast",
        "nano_banana": "nano-banana",
        "gpt_image_1_5": "gpt-image-2",  # fallback
    }
    return mapping.get(studio_id, studio_id.replace("_", "-"))
