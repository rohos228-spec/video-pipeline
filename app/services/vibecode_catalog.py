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
DEFAULT_TEXT_MODEL_ID = "gpt-5.6-sol"
DEFAULT_IMAGE_MODEL_ID = "gpt-image-2"

IMAGE_MODEL_TO_GENERATOR: dict[str, str] = {
    "gpt-image-2": "gpt_image_2",
    "gpt-image-2-vip": "gpt_image_2_vip",
    "nano-banana": "nano_banana",
    "nano-banana-2": "nano_banana_2",
    "nano-banana-pro": "nano_banana_pro",
    "nano-banana-2-lite": "nano_banana_2_lite",
}

VENDOR_ORDER = ("anthropic", "openai", "gemini", "xai", "moonshot", "images")
VENDOR_META: dict[str, dict[str, str]] = {
    "anthropic": {"id": "anthropic", "label": "Anthropic", "icon": "A"},
    "openai": {"id": "openai", "label": "OpenAI", "icon": "hex"},
    "gemini": {"id": "gemini", "label": "Gemini", "icon": "spark"},
    "xai": {"id": "xai", "label": "xAI", "icon": "X"},
    "moonshot": {"id": "moonshot", "label": "Moonshot", "icon": "moon"},
    "images": {"id": "images", "label": "Изображения", "icon": "image"},
}


def _vendor_of(model_id: str, is_image: bool) -> str:
    if is_image:
        return "images"
    mid = (model_id or "").strip().lower()
    if mid.startswith("claude"):
        return "anthropic"
    if mid.startswith("gemini"):
        return "gemini"
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
    display = str(raw.get("display_name") or mid)
    vendor = _vendor_of(mid, is_image)
    pricing = apply_markup(raw.get("pricing") if isinstance(raw.get("pricing"), dict) else {}, channel=channel)
    return {
        "id": mid,
        "label": display,
        "vendor": vendor,
        "kind": "image" if is_image else "text",
        "online": True,
        "resolution": _resolution_badge(display, mid),
        "pricing": pricing,
        "api_model": mid,
        "provider": "vibecode",
        "image_generator": IMAGE_MODEL_TO_GENERATOR.get(mid),
    }


def models_for_channel(
    raw_models: list[dict[str, Any]] | None = None,
    *,
    channel: str | None = None,
) -> list[dict[str, Any]]:
    src = raw_models if raw_models is not None else load_snapshot()
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in src:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "").strip()
        if not mid or mid in seen:
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
    for item in models_for_channel(channel=channel):
        if item["id"] == want:
            return item
    return None


def default_model_id_for_node_type(node_type: str | None) -> str:
    if (node_type or "").strip() in IMAGE_NODE_TYPES:
        return DEFAULT_IMAGE_MODEL_ID
    return DEFAULT_TEXT_MODEL_ID


def read_node_model_fields(node: dict[str, Any] | None) -> tuple[str | None, str]:
    if not isinstance(node, dict):
        return None, "stable"
    data = node.get("data") if isinstance(node.get("data"), dict) else node
    if not isinstance(data, dict):
        return None, "stable"
    mid = str(data.get("modelId") or data.get("model_id") or "").strip() or None
    return mid, "stable"


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

    return (
        resolve_image_generator_id(project, node_key=node_key, node_type=node_type)
        or DEFAULTS["image_generator"]
    )
