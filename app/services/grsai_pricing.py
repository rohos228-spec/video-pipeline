"""Цены Create / Grsai: 1 токен = $0.10 (10 центов).

Токены — учётная единица Studio. Для Grsai-моделей база берётся из
credits каталога (нормализуем), для Outsee — из числового price в UI.
"""

from __future__ import annotations

from typing import Any

# Фиксированный курс для UI / отчётов.
TOKEN_USD = 0.10

# Базовые токены по slug (уже Studio-токены, не «сырые» Grsai credits).
# Ориентир: ~цена Grsai в USD / TOKEN_USD, с округлением вверх до 0.1.
_IMAGE_BASE_TOKENS: dict[str, float] = {
    "gpt-image-2": 0.5,  # ~$0.03–0.06
    "gpt-image-2-vip": 1.0,
    "nano-banana": 0.3,
    "nano-banana-fast": 0.3,
    "nano-banana-2-lite": 0.3,
    "nano-banana-2": 0.8,
    "nano-banana-pro": 1.2,
    "nano-banana-pro-vt": 1.2,
    "nano-banana-2-cl": 4.0,
    "nano-banana-2-2k-cl": 6.0,
    "nano-banana-2-4k-cl": 9.0,
    "nano-banana-pro-cl": 7.0,
    "nano-banana-pro-vip": 7.0,
    "nano-banana-pro-4k-vip": 12.0,
    "gpt-image-1.5": 3.0,
    "seedream-4.5": 2.0,
    "seedream-5-pro": 3.0,
    "seedream-5-lite": 2.0,
}

_VIDEO_BASE_TOKENS: dict[str, float] = {
    "sora-2": 0.8,  # ~$0.08
    "sora2-portrait": 0.8,
    "sora2-landscape": 0.8,
    "veo3.1-fast": 4.0,  # ~$0.40
    "veo3.1-pro": 4.0,
    "veo-3-1-lite": 4.0,
    "veo-3-fast": 4.0,
    "kling-3-0": 5.0,
    "kling-2-6": 4.0,
    "seedance-1-5-pro": 3.5,
    "seedance-2-0-global": 20.0,
}

_AUDIO_BASE_TOKENS: dict[str, float] = {
    "suno-5-5": 2.5,
    "elevenlabs-v3": 1.0,
}

# Grsai credits → studio tokens (для справки / API quote)
_GRSAI_CREDITS: dict[str, int] = {
    "gpt-image-2": 600,
    "gpt-image-2-vip": 1300,
    "nano-banana-pro": 1800,
    "nano-banana-2-lite": 440,
    "nano-banana-2": 1200,
    "nano-banana-pro-vt": 1800,
    "nano-banana-fast": 440,
    "nano-banana-pro-cl": 10000,
    "nano-banana-2-cl": 6000,
    "nano-banana-2-2k-cl": 9000,
    "nano-banana-2-4k-cl": 13000,
    "nano-banana-pro-4k-vip": 18000,
    "nano-banana-pro-vip": 10000,
    "nano-banana": 440,
}


def _round_tokens(n: float) -> float:
    """Округление до 0.1 токена, минимум 0.1 если > 0."""
    if n <= 0:
        return 0.0
    return max(0.1, round(n * 10) / 10)


def _image_multiplier(resolution: str | None) -> float:
    r = (resolution or "1K").strip().upper()
    if r == "4K":
        return 2.0
    if r in {"2K", "3K"}:
        return 1.5
    return 1.0


def _video_multiplier(*, duration: int | None, size: str | None, model: str) -> float:
    mult = 1.0
    d = int(duration or 10)
    if model.startswith("sora"):
        if d >= 15:
            mult *= 1.5
        if (size or "small").lower() == "large":
            mult *= 2.0
    elif model.startswith("veo"):
        # Veo на Grsai — фикс за ролик
        pass
    else:
        # outsee-ish: грубо пропорционально секундам от базы 5с
        if d > 0:
            mult *= max(0.5, d / 5.0)
    return mult


def quote_generation(
    *,
    media: str,
    model: str,
    resolution: str | None = "1K",
    duration: int | None = 10,
    size: str | None = "small",
    catalog_price: str | None = None,
) -> dict[str, Any]:
    """Вернуть токены + USD для выбранных параметров."""
    media = (media or "image").lower()
    model = (model or "").strip()
    base = 0.0
    source = "default"

    if media == "image":
        if model in _IMAGE_BASE_TOKENS:
            base = _IMAGE_BASE_TOKENS[model]
            source = "table"
        base *= _image_multiplier(resolution)
    elif media == "video":
        if model in _VIDEO_BASE_TOKENS:
            base = _VIDEO_BASE_TOKENS[model]
            source = "table"
        base *= _video_multiplier(duration=duration, size=size, model=model)
    else:
        if model in _AUDIO_BASE_TOKENS:
            base = _AUDIO_BASE_TOKENS[model]
            source = "table"

    # Fallback: разобрать catalog price ("3", "от 2.5", "от 0.08")
    if base <= 0 and catalog_price:
        parsed = _parse_catalog_price(catalog_price)
        if parsed is not None:
            base = parsed
            source = "catalog_price"
            if media == "image":
                base *= _image_multiplier(resolution)
            elif media == "video":
                base *= _video_multiplier(duration=duration, size=size, model=model)

    if base <= 0:
        base = 1.0
        source = "fallback"

    tokens = _round_tokens(base)
    usd = round(tokens * TOKEN_USD, 2)
    credits = _GRSAI_CREDITS.get(model)
    return {
        "media": media,
        "model": model,
        "tokens": tokens,
        "usd": usd,
        "token_usd": TOKEN_USD,
        "label": f"{_fmt_tokens(tokens)} ток · ${_fmt_usd(usd)}",
        "label_short": f"{_fmt_tokens(tokens)} ток",
        "usd_label": f"${_fmt_usd(usd)}",
        "grsai_credits": credits,
        "source": source,
        "params": {
            "resolution": resolution,
            "duration": duration,
            "size": size,
        },
    }


def _parse_catalog_price(raw: str) -> float | None:
    s = (raw or "").strip().lower().replace("от ", "").replace("от", "")
    s = s.replace("/с", "").replace(",", ".").strip()
    # взять первое число
    num = ""
    for ch in s:
        if ch.isdigit() or ch == ".":
            num += ch
        elif num:
            break
    if not num:
        return None
    try:
        val = float(num)
    except ValueError:
        return None
    if val <= 0:
        return None
    # мелкие значения (< 1) считаем уже USD → токены
    if val < 1:
        return val / TOKEN_USD
    return val


def _fmt_tokens(n: float) -> str:
    if abs(n - int(n)) < 1e-9:
        return str(int(n))
    return f"{n:.1f}".rstrip("0").rstrip(".")


def _fmt_usd(n: float) -> str:
    return f"{n:.2f}"
