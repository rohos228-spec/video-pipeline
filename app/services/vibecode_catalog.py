"""Каталог моделей vibecode.moe для выбора на ноде.

Цены в UI = сырые vibecode × PRICE_MARKUP (дорогой канал).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

PRICE_MARKUP = 3.0
PRICE_MARKUP_STABLE = PRICE_MARKUP

_SNAPSHOT_PATH = Path(__file__).resolve().parent / "vibecode_models_snapshot.json"

IMAGE_NODE_TYPES = frozenset({"images", "hero", "items", "hitl_images"})
VIDEO_NODE_TYPES = frozenset({"videos", "hitl_videos"})
DEFAULT_TEXT_MODEL_ID = "gpt-5.6-sol"
DEFAULT_IMAGE_MODEL_ID = "gpt-image-2-vip"
DEFAULT_VIDEO_MODEL_ID = "veo-3-1-lite"
HIDDEN_IMAGE_IDS = frozenset({
    "gpt-image-2",
    "nano-banana-pro",
    "nano-banana",
    "seedream-4.5",
    "seedream-5-lite",
    "gpt-image-1.5",
})
IMAGE_MODEL_ALIASES = {"gpt-image-2": "gpt-image-2-vip"}

IMAGE_MODEL_TO_GENERATOR: dict[str, str] = {
    "gpt-image-2": "gpt_image_2_vip",
    "gpt-image-2-vip": "gpt_image_2_vip",
    "nano-banana": "nano_banana",
    "nano-banana-2": "nano_banana_2",
    "nano-banana-2-lite": "nano_banana_2_lite",
    "nano-banana-pro": "nano_banana_pro",
    "nano-banana-fast": "nano_banana_fast",
    "flux-2-pro": "flux_2_pro",
    "seedream-5-pro": "seedream_5_pro",
    "z-image": "z_image",
    "qwen3-image": "qwen3_image",
    "topaz-image-upscale": "topaz_image_upscale",
    "recraft-remove-bg": "recraft_remove_bg",
    "recraft-crisp-upscale": "recraft_crisp_upscale",
}
VIDEO_MODEL_TO_GENERATOR: dict[str, str] = {
    "veo-3-1-lite": "veo_3_1_lite",
    "seedance-2-5": "seedance_2_5",
    "seedance-1-5-pro": "seedance_1_5_pro",
    "kling-3-0": "kling_3_0",
    "kling-v3-turbo-t2v": "kling_v3_turbo_t2v",
    "kling-v3-turbo-i2v": "kling_v3_turbo_i2v",
    "kling-3-0-omni-t2v": "kling_3_0_omni_t2v",
    "hailuo-2-3-i2v": "hailuo_2_3_i2v",
    "wan-2-7-t2v": "wan_2_7_t2v",
    "pixverse-v6-t2v": "pixverse_v6_t2v",
    "topaz-video-upscale": "topaz_video_upscale",
    "kling-2-6": "kling_2_6",
}

IMAGE_CATALOG_EXTRA: list[dict[str, Any]] = [
    {
        "id": "flux-2-pro",
        "display_name": "Flux 2 Pro",
        "is_image": True,
        "is_video": False,
        "owned_by": "kie",
        "pricing": {"currency": "usd", "usd_per_image": 0.05},
        "image_generator": "flux_2_pro",
    },
    {
        "id": "seedream-5-pro",
        "display_name": "ByteDance Seedream 5 Pro",
        "is_image": True,
        "is_video": False,
        "owned_by": "kie",
        "pricing": {"currency": "usd", "usd_per_image": 0.045},
        "image_generator": "seedream_5_pro",
    },
    {
        "id": "z-image",
        "display_name": "Z-Image",
        "is_image": True,
        "is_video": False,
        "owned_by": "kie",
        "pricing": {"currency": "usd", "usd_per_image": 0.02},
        "image_generator": "z_image",
    },
    {
        "id": "qwen3-image",
        "display_name": "Alibaba Qwen Image 3",
        "is_image": True,
        "is_video": False,
        "owned_by": "kie",
        "pricing": {"currency": "usd", "usd_per_image": 0.035},
        "image_generator": "qwen3_image",
    },
]

VENDOR_ORDER = ("anthropic", "openai", "gemini", "deepseek", "xai", "images", "video")
VENDOR_META: dict[str, dict[str, str]] = {
    "anthropic": {"id": "anthropic", "label": "Anthropic", "icon": "A"},
    "openai": {"id": "openai", "label": "OpenAI", "icon": "hex"},
    "gemini": {"id": "gemini", "label": "Gemini", "icon": "spark"},
    "deepseek": {"id": "deepseek", "label": "DeepSeek", "icon": "D"},
    "xai": {"id": "xai", "label": "xAI", "icon": "X"},
    "images": {"id": "images", "label": "Изображения", "icon": "image"},
    "video": {"id": "video", "label": "Видео", "icon": "image"},
}

VIDEO_CATALOG_RAW: list[dict[str, Any]] = [
    {
        "id": "seedance-2-5",
        "display_name": "Seedance 2.5 (ByteDance)",
        "is_image": False,
        "is_video": True,
        "owned_by": "kie",
        "video_generator": "seedance_2_5",
        "pricing": {"currency": "usd"},
    },
    {
        "id": "seedance-1-5-pro",
        "display_name": "Seedance 1.5 Pro",
        "is_image": False,
        "is_video": True,
        "owned_by": "kie",
        "video_generator": "seedance_1_5_pro",
        "pricing": {"currency": "usd"},
    },
    {
        "id": "kling-3-0",
        "display_name": "Kling 3.0 Pro (1080p + звук)",
        "is_image": False,
        "is_video": True,
        "owned_by": "kie",
        "video_generator": "kling_3_0",
        "pricing": {"currency": "usd"},
    },
    {
        "id": "kling-v3-turbo-t2v",
        "display_name": "Kling 3.0 Turbo (текст)",
        "is_image": False,
        "is_video": True,
        "owned_by": "kie",
        "video_generator": "kling_v3_turbo_t2v",
        "pricing": {"currency": "usd"},
    },
    {
        "id": "kling-v3-turbo-i2v",
        "display_name": "Kling 3.0 Turbo (оживление фото)",
        "is_image": False,
        "is_video": True,
        "owned_by": "kie",
        "video_generator": "kling_v3_turbo_i2v",
        "pricing": {"currency": "usd"},
    },
    {
        "id": "kling-3-0-omni-t2v",
        "display_name": "Kling 3.0 Omni (со звуком)",
        "is_image": False,
        "is_video": True,
        "owned_by": "kie",
        "video_generator": "kling_3_0_omni_t2v",
        "pricing": {"currency": "usd"},
    },
    {
        "id": "hailuo-2-3-i2v",
        "display_name": "MiniMax Hailuo 2.3 (фото)",
        "is_image": False,
        "is_video": True,
        "owned_by": "kie",
        "video_generator": "hailuo_2_3_i2v",
        "pricing": {"currency": "usd"},
    },
    {
        "id": "wan-2-7-t2v",
        "display_name": "Alibaba WAN 2.7 (текст)",
        "is_image": False,
        "is_video": True,
        "owned_by": "kie",
        "video_generator": "wan_2_7_t2v",
        "pricing": {"currency": "usd"},
    },
    {
        "id": "pixverse-v6-t2v",
        "display_name": "PixVerse V6 (текст)",
        "is_image": False,
        "is_video": True,
        "owned_by": "kie",
        "video_generator": "pixverse_v6_t2v",
        "pricing": {"currency": "usd"},
    },
    {
        "id": "topaz-video-upscale",
        "display_name": "Topaz Video AI (апскейл)",
        "is_image": False,
        "is_video": True,
        "owned_by": "kie",
        "video_generator": "topaz_video_upscale",
        "pricing": {"currency": "usd"},
    },
    {
        "id": "veo-3-1-lite",
        "display_name": "Veo 3.1 Lite",
        "is_image": False,
        "is_video": True,
        "owned_by": "outsee",
        "video_generator": "veo_3_1_lite",
        "pricing": {"currency": "usd"},
    },
]


def _vendor_of(model_id: str, is_image: bool, is_video: bool = False) -> str:
    if is_video:
        return "video"
    if is_image:
        return "images"
    mid = (model_id or "").strip().lower()
    if mid.startswith("claude"):
        return "anthropic"
    if mid.startswith("gemini"):
        return "gemini"
    if mid.startswith("deepseek"):
        return "deepseek"
    if mid.startswith("grok"):
        return "xai"
    if mid.startswith("kimi"):
        return "moonshot"
    return "openai"


def _resolution_badge(display_name: str, model_id: str) -> str | None:
    name = f"{display_name} {model_id}".lower()
    if "1k/2k/4k" in name or model_id in {"nano-banana-2", "nano-banana-pro"}:
        return "1K/2K/4K"
    return None


@lru_cache(maxsize=1)
def load_snapshot() -> list[dict[str, Any]]:
    if not _SNAPSHOT_PATH.is_file():
        return []
    try:
        data = json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("vibecode snapshot read failed: {}", e)
        return []
    return data if isinstance(data, list) else []


def markup_for_channel(channel: str | None = None) -> float:
    """Единственный канал — дорогой (×3). Аргумент оставлен для совместимости."""
    _ = channel
    return PRICE_MARKUP


def _round_price(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return 0.0
    return round(n, 6)


def apply_markup(
    pricing: dict[str, Any] | None,
    *,
    channel: str | None = None,
) -> dict[str, Any]:
    raw = dict(pricing or {})
    factor = markup_for_channel(channel)
    out: dict[str, Any] = {"currency": raw.get("currency") or "usd", "markup": factor}
    for key in (
        "input_usd_per_m",
        "output_usd_per_m",
        "cache_read_usd_per_m",
        "cache_create_usd_per_m",
        "usd_per_image",
    ):
        if key not in raw or raw[key] is None:
            continue
        marked = _round_price(float(raw[key]) * factor)
        if marked is not None:
            out[key] = marked
    return out


def normalize_model(raw: dict[str, Any], *, channel: str | None = None) -> dict[str, Any]:
    mid = str(raw.get("id") or "").strip()
    is_image = bool(raw.get("is_image"))
    is_video = bool(raw.get("is_video")) or mid in VIDEO_MODEL_TO_GENERATOR
    display = str(raw.get("display_name") or mid)
    if mid == "gpt-image-2-vip":
        display = "GPT Image 2"
    vendor = _vendor_of(mid, is_image, is_video)
    if is_video:
        kind = "video"
    elif is_image:
        kind = "image"
    else:
        kind = "text"
    pricing = apply_markup(raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}, channel=channel)
    provider = "vibecode"
    if is_video:
        from app.services.media_route import video_provider_for

        provider = video_provider_for(mid)
    elif is_image:
        from app.services.media_route import image_provider_for

        provider = image_provider_for(mid)
    return {
        "id": mid,
        "label": display,
        "vendor": vendor,
        "kind": kind,
        "online": True,
        "resolution": _resolution_badge(display, mid),
        "pricing": pricing,
        "api_model": mid,
        "provider": provider,
        "image_generator": IMAGE_MODEL_TO_GENERATOR.get(mid),
        "video_generator": VIDEO_MODEL_TO_GENERATOR.get(mid),
    }


def models_for_channel(
    raw_models: list[dict[str, Any]] | None = None,
    *,
    channel: str | None = None,
) -> list[dict[str, Any]]:
    src = list(raw_models if raw_models is not None else load_snapshot())
    src.extend(IMAGE_CATALOG_EXTRA)
    src.extend(VIDEO_CATALOG_RAW)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in src:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "").strip()
        if not mid or mid in seen or mid in HIDDEN_IMAGE_IDS:
            continue
        seen.add(mid)
        out.append(normalize_model(item, channel=channel))
    return out


def grouped_catalog(
    raw_models: list[dict[str, Any]] | None = None,
    *,
    channel: str | None = None,
) -> dict[str, Any]:
    models = models_for_channel(raw_models, channel=channel)
    vendors: list[dict[str, Any]] = []
    for vid in VENDOR_ORDER:
        items = [m for m in models if m["vendor"] == vid]
        if not items:
            continue
        meta = VENDOR_META[vid]
        vendors.append({**meta, "count": len(items), "models": items})
    extra = [m for m in models if m["vendor"] not in VENDOR_ORDER]
    if extra:
        vendors.append(
            {
                "id": "other",
                "label": "Другие",
                "icon": "dot",
                "count": len(extra),
                "models": extra,
            }
        )
    return {
        "channel": "stable",
        "markup": PRICE_MARKUP,
        "markup_stable": PRICE_MARKUP,
        "vendors": vendors,
        "models": models,
    }


def find_model(model_id: str | None, *, channel: str | None = None) -> dict[str, Any] | None:
    want = (model_id or "").strip()
    if not want:
        return None
    want = IMAGE_MODEL_ALIASES.get(want, want)
    for item in models_for_channel(channel=channel):
        if item["id"] == want:
            return item
    return None


def default_model_id_for_node_type(node_type: str | None) -> str:
    nt = (node_type or "").strip()
    if nt in IMAGE_NODE_TYPES:
        return DEFAULT_IMAGE_MODEL_ID
    if nt in VIDEO_NODE_TYPES:
        return DEFAULT_VIDEO_MODEL_ID
    return DEFAULT_TEXT_MODEL_ID


def read_node_model_fields(node: dict[str, Any] | None) -> tuple[str | None, str]:
    if not isinstance(node, dict):
        return None, "stable"
    data = node.get("data") if isinstance(node.get("data"), dict) else node
    if not isinstance(data, dict):
        return None, "stable"
    mid = str(data.get("modelId") or data.get("model_id") or "").strip() or None
    return mid, "stable"


def _node_data_dict(node: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {}
    data = node.get("data") if isinstance(node.get("data"), dict) else node
    return data if isinstance(data, dict) else {}


def read_node_media_fields(node: dict[str, Any] | None) -> dict[str, str | None]:
    """Параметры media-пикера с ноды: resolution / quality / aspect."""
    data = _node_data_dict(node)
    res = str(data.get("imageResolution") or data.get("image_resolution") or "").strip() or None
    quality = str(data.get("imageQuality") or data.get("image_quality") or "").strip() or None
    aspect = str(data.get("aspectRatio") or data.get("aspect_ratio") or "").strip() or None
    if aspect and ":" in aspect:
        aspect_id = aspect.replace(":", "_")
    elif aspect:
        aspect_id = aspect
    else:
        aspect_id = None
    return {
        "image_resolution": res.lower() if res else None,
        "image_quality": quality.lower() if quality else None,
        "aspect_ratio": aspect_id,
        "aspect_slug": aspect.replace("_", ":") if aspect else None,
    }


def resolve_node_media_settings(
    project: Any,
    *,
    node_key: str | None = None,
    node_type: str = "images",
) -> dict[str, Any]:
    """Настройки картинки: нода перекрывает проект."""
    from app.generation_options import (
        ASPECT_RATIOS_BY_ID,
        DEFAULTS,
        IMAGE_RESOLUTIONS_BY_ID,
        clamp_image_resolution_id,
        resolve_image_quality_slug,
    )

    meta = getattr(project, "meta", None)
    node = find_canvas_node(meta, node_key=node_key, node_type=node_type)
    fields = read_node_media_fields(node)
    img_gid = effective_image_generator_id(project, node_key=node_key, node_type=node_type)

    res_raw = fields["image_resolution"] or getattr(project, "image_resolution", None)
    res_id = clamp_image_resolution_id(img_gid, res_raw)
    ir = IMAGE_RESOLUTIONS_BY_ID.get(res_id)

    aspect_id = fields["aspect_ratio"] or getattr(project, "aspect_ratio", None) or DEFAULTS["aspect_ratio"]
    ar = ASPECT_RATIOS_BY_ID.get(aspect_id)
    aspect_slug = (
        fields["aspect_slug"]
        or (ar.outsee_slug if ar else None)
        or "9:16"
    )

    quality_raw = fields["image_quality"] or getattr(project, "image_quality", None)
    quality_slug = resolve_image_quality_slug(img_gid, quality_raw)
    # Nano Banana и прочие: если на ноде явно выбрали детализацию — всё равно отдаём slug.
    if quality_slug is None and fields["image_quality"]:
        from app.generation_options import IMAGE_QUALITIES_BY_ID

        choice = IMAGE_QUALITIES_BY_ID.get(fields["image_quality"])
        quality_slug = choice.outsee_slug if choice else None

    return {
        "image_generator_id": img_gid,
        "resolution_id": res_id,
        "resolution_slug": ir.outsee_slug if ir else None,
        "aspect_id": aspect_id,
        "aspect_slug": aspect_slug,
        "quality_id": quality_raw,
        "quality_slug": quality_slug,
    }


def find_canvas_node(
    meta: dict[str, Any] | None,
    *,
    node_key: str | None = None,
    node_type: str | None = None,
) -> dict[str, Any] | None:
    from app.services.canvas_graph import canvas_graph_from_meta

    cg = canvas_graph_from_meta(meta)
    if not cg:
        return None
    nodes = cg.get("nodes") or []
    if node_key:
        want = str(node_key).strip()
        for n in nodes:
            if isinstance(n, dict) and str(n.get("id") or "").strip() == want:
                return n
    if node_type:
        want_t = str(node_type).strip()
        from app.services.excel_gpt_node import effective_node_type

        for n in nodes:
            if not isinstance(n, dict):
                continue
            typ = str(n.get("type") or "").strip()
            if typ == want_t or effective_node_type(n) == want_t:
                return n
    return None


def resolve_node_choice(
    meta: dict[str, Any] | None,
    *,
    node_key: str | None = None,
    node_type: str | None = None,
) -> dict[str, Any] | None:
    node = find_canvas_node(meta, node_key=node_key, node_type=node_type)
    mid, channel = read_node_model_fields(node)
    if not mid:
        mid = default_model_id_for_node_type(
            str((node or {}).get("type") or node_type or "")
        )
    found = find_model(mid, channel=channel)
    if found:
        found = {**found, "channel": channel}
    return found


def resolve_image_generator_id(
    project: Any,
    *,
    node_key: str | None = None,
    node_type: str = "images",
) -> str | None:
    """id из generation_options.IMAGE_GENERATORS, если на ноде выбрана картинка."""
    meta = getattr(project, "meta", None)
    choice = resolve_node_choice(meta, node_key=node_key, node_type=node_type)
    if choice and choice.get("kind") == "image" and choice.get("image_generator"):
        return str(choice["image_generator"])
    gid = getattr(project, "image_generator", None)
    return str(gid).strip() if gid else None


def effective_image_generator_id(
    project: Any,
    *,
    node_key: str | None = None,
    node_type: str = "images",
) -> str:
    from app.generation_options import DEFAULTS

    gid = (
        resolve_image_generator_id(project, node_key=node_key, node_type=node_type)
        or DEFAULTS["image_generator"]
    )
    if gid == "gpt_image_2":
        return "gpt_image_2_vip"
    return gid


def resolve_video_generator_id(
    project: Any,
    *,
    node_key: str | None = None,
    node_type: str = "videos",
) -> str | None:
    meta = getattr(project, "meta", None)
    node = find_canvas_node(meta, node_key=node_key, node_type=node_type)
    mid, _channel = read_node_model_fields(node)
    if mid:
        found = find_model(mid)
        if found and found.get("video_generator"):
            return str(found["video_generator"])
    gid = getattr(project, "video_generator", None)
    return str(gid).strip() if gid else None


def effective_video_generator_id(
    project: Any,
    *,
    node_key: str | None = None,
    node_type: str = "videos",
) -> str:
    from app.generation_options import DEFAULTS

    return (
        resolve_video_generator_id(project, node_key=node_key, node_type=node_type)
        or DEFAULTS["video_generator"]
    )
