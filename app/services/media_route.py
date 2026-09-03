"""Per-model media backend: Outsee vs KIE.

Глобальные IMAGE_PROVIDER / VIDEO_PROVIDER остаются дефолтом для прочих
моделей.

- GPT Image 2 / Nano Banana 2 → OUTSEE_API_KEY / KIE_API_KEY
- Veo 3.1 Lite → OUTSEE_API_KEY
- Kling 2.6 / 3.0 → KIE_API_KEY
"""

from __future__ import annotations

from app.settings import settings

OUTSEE_IMAGE_IDS = frozenset({
    "gpt-image-2-vip",
    "nano-banana-2",
})
KIE_IMAGE_IDS = frozenset({
    "flux-2-pro",
    "seedream-5-pro",
    "z-image",
    "qwen3-image",
})
OUTSEE_VIDEO_IDS = frozenset({
    "veo-3-1-lite",
})
KIE_VIDEO_IDS = frozenset({
    "seedance-2-5",
    "seedance-1-5-pro",
    "kling-3-0",
    "kling-v3-turbo-t2v",
    "kling-v3-turbo-i2v",
    "kling-3-0-omni-t2v",
    "hailuo-2-3-i2v",
    "wan-2-7-t2v",
    "pixverse-v6-t2v",
    "topaz-video-upscale",
    "kling-2-6",
})

# Старый Slow и studio-id → канонический slug.
_ALIASES = {
    "gpt-image-2": "gpt-image-2-vip",
    "gpt-image-2-vip": "gpt-image-2-vip",
    "nano-banana-2": "nano-banana-2",
    "veo-3-1-lite": "veo-3-1-lite",
    "veo-3-1-fast": "veo-3-1-lite",
    "kling-2-6": "kling-2-6",
}


def canonical_media_id(raw: str | None) -> str:
    s = (raw or "").strip().lower().replace("_", "-")
    if not s:
        return ""
    return _ALIASES.get(s, s)


def is_nano_banana_pro(model_slug: str | None) -> bool:
    """Любой slug Nano Banana Pro (pro / vt / cl / vip / 4k-vip)."""
    cid = canonical_media_id(model_slug)
    return cid == "nano-banana-pro" or cid.startswith("nano-banana-pro-")


def image_provider_for(model_slug: str | None) -> str:
    cid = canonical_media_id(model_slug)
    if cid in KIE_IMAGE_IDS:
        return "kie"
    if cid in OUTSEE_IMAGE_IDS:
        return "outsee"
    return (getattr(settings, "image_provider", None) or "outsee").strip().lower() or "outsee"


def video_provider_for(model_slug: str | None) -> str:
    cid = canonical_media_id(model_slug)
    if cid in KIE_VIDEO_IDS:
        return "kie"
    if cid in OUTSEE_VIDEO_IDS:
        return "outsee"
    return (getattr(settings, "video_provider", None) or "outsee").strip().lower() or "outsee"
