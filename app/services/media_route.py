"""Per-model media backend: Outsee vs Grsai vs kie Kling.

Глобальные IMAGE_PROVIDER / VIDEO_PROVIDER остаются дефолтом для прочих
моделей. Жёстко:

- GPT Image 2 / Nano Banana 2 → OUTSEE_API_KEY
- Nano Banana Pro и все её варианты → никогда Outsee (даже если IMAGE_PROVIDER=outsee)
- Veo 3.1 Lite → OUTSEE_API_KEY
- Kling 2.6 → KIE_API_KEY
"""

from __future__ import annotations

from app.settings import settings

# После нормализации (_ → -) и алиасов.
OUTSEE_IMAGE_IDS = frozenset({
    "gpt-image-2-vip",
    "nano-banana-2",
    "nano-banana-2-lite",
})
OUTSEE_VIDEO_IDS = frozenset({
    "veo-3-1-lite",
})
KIE_VIDEO_IDS = frozenset({
    "kling-2-6",
})

# Старый Slow и studio-id → канонический slug.
_ALIASES = {
    "gpt-image-2": "gpt-image-2-vip",
    "gpt-image-2-vip": "gpt-image-2-vip",
    "nano-banana-2": "nano-banana-2",
    "nano-banana-2-lite": "nano-banana-2-lite",
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
    if is_nano_banana_pro(model_slug):
        return "grsai"
    cid = canonical_media_id(model_slug)
    if cid in OUTSEE_IMAGE_IDS:
        return "outsee"
    return (getattr(settings, "image_provider", None) or "grsai").strip().lower() or "grsai"


def video_provider_for(model_slug: str | None) -> str:
    cid = canonical_media_id(model_slug)
    if cid in KIE_VIDEO_IDS:
        return "kie"
    if cid in OUTSEE_VIDEO_IDS:
        return "outsee"
    return (getattr(settings, "video_provider", None) or "grsai").strip().lower() or "grsai"
