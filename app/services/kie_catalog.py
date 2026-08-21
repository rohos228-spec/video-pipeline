"""Каталог моделей kie.ai для вкладки «Генерация» (индивидуальная генерация).

Источники (20.08.2026):
  - параметры: OpenAPI-спеки docs.kie.ai (market/*, veo3-api, suno-api, runway-api)
  - цены: страницы моделей kie.ai (pricingDesc) — кредиты и $.
Курс kie: 1 кредит = $0.005 (kie.ai/dashboard/billing).

Структура модели:
  id, label, category, desc, api ("jobs" | "veo" | "suno" | "runway"),
  model (для jobs — поле model), endpoint (для suno/runway/veo),
  result ("video" | "image" | "audio"), fields[], pricing{}.

Field: name, label, kind, required, default, options[{id,label}], min, max,
  step, max_items, accept, desc, show_if {field: value}.
kind: text | textarea | select | toggle | number | images | videos | audios |
  dialogue (строки «голос | текст»).

pricing: unit = gen | sec | 1k_chars; rules — первое совпадение по when
(точное сравнение; спец-ключи _has_images/_has_videos — по файловым полям);
default — fallback кредитов; note — текстовая расшифровка с kie.ai.
"""

from __future__ import annotations

import contextlib
import math
from typing import Any

CREDIT_USD = 0.005  # 1 кредит kie.ai = $0.005

CATEGORIES: list[dict[str, str]] = [
    {"id": "video", "label": "Видео"},
    {"id": "image", "label": "Картинки"},
    {"id": "music", "label": "Музыка (Suno)"},
    {"id": "sound", "label": "Звуки"},
    {"id": "voice", "label": "Голос / TTS"},
    {"id": "tools", "label": "Апскейл и утилиты"},
]

_ASPECT_VIDEO = ["16:9", "9:16", "1:1"]
_ASPECT_WIDE = ["1:1", "4:3", "3:4", "16:9", "9:16", "21:9", "adaptive"]
_ASPECT_IMG = ["auto", "1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16", "21:9"]
_RES_IMG = ["1K", "2K", "4K"]


def _f(
    name: str,
    label: str,
    kind: str,
    *,
    required: bool = False,
    default: Any = None,
    options: list[str] | None = None,
    min: float | None = None,  # noqa: A002
    max: float | None = None,  # noqa: A002
    step: float | None = None,
    max_items: int | None = None,
    desc: str = "",
    show_if: dict[str, Any] | None = None,
    max_len: int | None = None,
    omit_values: list[Any] | None = None,
) -> dict[str, Any]:
    f: dict[str, Any] = {
        "name": name,
        "label": label,
        "kind": kind,
        "required": required,
        "desc": desc,
    }
    if default is not None:
        f["default"] = default
    if options:
        f["options"] = options
    if min is not None:
        f["min"] = min
    if max is not None:
        f["max"] = max
    if step is not None:
        f["step"] = step
    if max_items is not None:
        f["max_items"] = max_items
    if show_if:
        f["show_if"] = show_if
    if max_len is not None:
        f["max_len"] = max_len
    if omit_values:
        f["omit_values"] = list(omit_values)
    return f


def _prompt(label: str = "Промпт", **kw: Any) -> dict[str, Any]:
    kw.setdefault("required", True)
    return _f("prompt", label, "textarea", **kw)


def _duration(def_: float = 5, min_: float = 1, max_: float = 15, **kw: Any) -> dict[str, Any]:
    return _f(
        "duration", "Длительность, сек", "number",
        default=def_, min=min_, max=max_, step=1, **kw,
    )


def _aspect(opts: list[str] | None = None, def_: str = "16:9") -> dict[str, Any]:
    return _f("aspect_ratio", "Соотношение сторон", "select", options=opts or _ASPECT_VIDEO, default=def_)


def _res(opts: list[str], def_: str) -> dict[str, Any]:
    return _f("resolution", "Разрешение", "select", options=opts, default=def_)


def _audio_toggle(def_: bool = False) -> dict[str, Any]:
    return _f("generate_audio", "Генерировать звук", "toggle", default=def_)


def _seed() -> dict[str, Any]:
    return _f("seed", "Seed (0 — случайно)", "number", min=0, max=2147483647, step=1)


MODELS: list[dict[str, Any]] = [
    # ============================ ВИДЕО ============================
    {
        "id": "veo-3-1",
        "label": "Veo 3.1 (Lite / Fast / Quality)",
        "category": "video",
        "api": "veo",
        "endpoint": "/api/v1/veo/generate",
        "result": "video",
        "desc": "Google Veo 3.1: text→video, image→video (1–2 кадра), reference→video (1–3 картинки, Fast/Lite). Звук по умолчанию.",
        "fields": [
            _prompt(),
            _f("model", "Режим", "select", options=["veo3_lite", "veo3_fast", "veo3"], default="veo3_lite",
               desc="Lite — дешево/массово, Fast — баланс, Quality — максимум"),
            _f("generationType", "Тип генерации", "select",
               options=["TEXT_2_VIDEO", "FIRST_AND_LAST_FRAMES_2_VIDEO", "REFERENCE_2_VIDEO"],
               default="TEXT_2_VIDEO",
               desc="REFERENCE — только Fast/Lite, 1–3 референс-картинки, 8 сек"),
            _aspect(["16:9", "9:16", "Auto"], "16:9"),
            _res(["720p", "1080p", "4k"], "720p"),
            _f("duration", "Длительность, сек", "select", options=["4", "6", "8"], default="8"),
            _f("imageUrls", "Кадры/референсы", "images", max_items=3,
               desc="1 кадр — оживление; 2 — первый+последний; 1–3 — референсы (REFERENCE_2_VIDEO)"),
            _f("watermark", "Вотермарка (текст)", "text"),
            _f("enableTranslation", "Переводить промпт на англ.", "toggle", default=True),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"model": "veo3_lite", "resolution": "720p"}, "credits": 30},
                {"when": {"model": "veo3_lite", "resolution": "1080p"}, "credits": 35},
                {"when": {"model": "veo3_lite", "resolution": "4k"}, "credits": 150},
                {"when": {"model": "veo3_fast", "resolution": "720p"}, "credits": 60},
                {"when": {"model": "veo3_fast", "resolution": "1080p"}, "credits": 65},
                {"when": {"model": "veo3_fast", "resolution": "4k"}, "credits": 180},
                {"when": {"model": "veo3", "resolution": "720p"}, "credits": 250},
                {"when": {"model": "veo3", "resolution": "1080p"}, "credits": 255},
                {"when": {"model": "veo3", "resolution": "4k"}, "credits": 370},
            ],
            "default": 60,
            "note": "за видео: Lite 30/35/150, Fast 60/65/180, Quality 250/255/370 кр (720p/1080p/4K)",
        },
    },
    {
        "id": "seedance-2-5",
        "label": "Seedance 2.5",
        "category": "video",
        "api": "jobs",
        "model": "bytedance/seedance-2-5",
        "result": "video",
        "desc": "ByteDance: до 30 сек, мультимодальные референсы (до 30 картинок / 10 видео / 10 аудио), первый+последний кадр.",
        "fields": [
            _prompt(),
            _f("first_frame_url", "Первый кадр", "images", max_items=1),
            _f("last_frame_url", "Последний кадр", "images", max_items=1,
               desc="Только вместе с первым кадром"),
            _f("reference_image_urls", "Референс-картинки", "images", max_items=30,
               desc="Несовместимо с первым/последним кадром"),
            _f("reference_video_urls", "Референс-видео", "videos", max_items=10,
               desc="480p/720p, 2–30 сек каждое, суммарно ≤30 сек"),
            _f("reference_audio_urls", "Референс-аудио", "audios", max_items=10,
               desc="wav/mp3, 2–30 сек каждое"),
            _res(["480p", "720p", "1080p"], "720p"),
            _aspect(_ASPECT_WIDE, "adaptive"),
            _duration(5, 4, 30, desc="-1 — автоматический выбор длительности"),
            _audio_toggle(True),
            _f("return_last_frame", "Вернуть последний кадр", "toggle", default=False),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "480p", "_has_videos": True}, "credits": 17},
                {"when": {"resolution": "480p"}, "credits": 28},
                {"when": {"resolution": "720p", "_has_videos": True}, "credits": 38},
                {"when": {"resolution": "720p"}, "credits": 63},
                {"when": {"resolution": "1080p", "_has_videos": True}, "credits": 68.5},
                {"when": {"resolution": "1080p"}, "credits": 114},
            ],
            "default": 63,
            "note": "кр/сек: с видеовходом дешевле (17/38/68.5), без — 28/63/114 (480p/720p/1080p)",
        },
    },
    {
        "id": "seedance-2-0",
        "label": "Seedance 2.0",
        "category": "video",
        "api": "jobs",
        "model": "bytedance/seedance-2",
        "result": "video",
        "desc": "До 15 сек, до 4K, референсы (9 картинок / 3 видео / 3 аудио), веб-поиск.",
        "fields": [
            _prompt(),
            _f("first_frame_url", "Первый кадр", "images", max_items=1),
            _f("last_frame_url", "Последний кадр", "images", max_items=1),
            _f("reference_image_urls", "Референс-картинки", "images", max_items=9),
            _f("reference_video_urls", "Референс-видео", "videos", max_items=3),
            _f("reference_audio_urls", "Референс-аудио", "audios", max_items=3),
            _res(["480p", "720p", "1080p", "4k"], "720p"),
            _aspect(_ASPECT_WIDE, "16:9"),
            _duration(5, 4, 15),
            _audio_toggle(True),
            _f("return_last_frame", "Вернуть последний кадр", "toggle", default=False),
            _f("web_search", "Веб-поиск", "toggle", default=False),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "480p", "_has_videos": True}, "credits": 11.5},
                {"when": {"resolution": "480p"}, "credits": 19},
                {"when": {"resolution": "720p", "_has_videos": True}, "credits": 25},
                {"when": {"resolution": "720p"}, "credits": 41},
                {"when": {"resolution": "1080p", "_has_videos": True}, "credits": 62},
                {"when": {"resolution": "1080p"}, "credits": 102},
                {"when": {"resolution": "4k", "_has_videos": True}, "credits": 128},
                {"when": {"resolution": "4k"}, "credits": 208},
            ],
            "default": 41,
            "note": "кр/сек: 480p 11.5/19, 720p 25/41, 1080p 62/102, 4K 128/208 (с видео/без)",
        },
    },
    {
        "id": "seedance-2-0-fast",
        "label": "Seedance 2.0 Fast",
        "category": "video",
        "api": "jobs",
        "model": "bytedance/seedance-2-fast",
        "result": "video",
        "desc": "Быстрый и дешёвый Seedance 2.0 для черновиков.",
        "fields": [
            _prompt(),
            _f("first_frame_url", "Первый кадр", "images", max_items=1),
            _f("last_frame_url", "Последний кадр", "images", max_items=1),
            _f("reference_image_urls", "Референс-картинки", "images", max_items=9),
            _f("reference_video_urls", "Референс-видео", "videos", max_items=3),
            _f("reference_audio_urls", "Референс-аудио", "audios", max_items=3),
            _res(["480p", "720p"], "720p"),
            _aspect(_ASPECT_WIDE, "16:9"),
            _duration(5, 4, 15),
            _audio_toggle(True),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "480p", "_has_videos": True}, "credits": 6.8},
                {"when": {"resolution": "480p"}, "credits": 11.7},
                {"when": {"resolution": "720p", "_has_videos": True}, "credits": 15},
                {"when": {"resolution": "720p"}, "credits": 24.8},
            ],
            "default": 24.8,
            "note": "кр/сек: 480p 6.8/11.7, 720p 15/24.8 (с видео/без)",
        },
    },
    {
        "id": "seedance-2-0-mini",
        "label": "Seedance 2.0 Mini",
        "category": "video",
        "api": "jobs",
        "model": "bytedance/seedance-2-mini",
        "result": "video",
        "desc": "Самый дешёвый Seedance — массовые черновики.",
        "fields": [
            _prompt(),
            _f("first_frame_url", "Первый кадр", "images", max_items=1),
            _f("last_frame_url", "Последний кадр", "images", max_items=1),
            _f("reference_image_urls", "Референс-картинки", "images", max_items=9),
            _f("reference_video_urls", "Референс-видео", "videos", max_items=3),
            _res(["480p", "720p"], "720p"),
            _aspect(_ASPECT_WIDE, "16:9"),
            _duration(5, 4, 15),
            _audio_toggle(True),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "480p", "_has_videos": True}, "credits": 2.4},
                {"when": {"resolution": "480p"}, "credits": 3.8},
                {"when": {"resolution": "720p", "_has_videos": True}, "credits": 5},
                {"when": {"resolution": "720p"}, "credits": 8.2},
            ],
            "default": 8.2,
            "note": "кр/сек: 480p 2.4/3.8, 720p 5/8.2 (с видео/без)",
        },
    },
    {
        "id": "seedance-1-5-pro",
        "label": "Seedance 1.5 Pro",
        "category": "video",
        "api": "jobs",
        "model": "bytedance/seedance-1.5-pro",
        "result": "video",
        "desc": "Прошлое поколение Pro: стабильный, дешевле 2.x.",
        "fields": [
            _prompt(),
            _f("image_urls", "Кадры (1–2)", "images", max_items=2),
            _res(["480p", "720p", "1080p"], "720p"),
            _aspect(_ASPECT_VIDEO, "16:9"),
            _duration(5, 4, 12),
            _audio_toggle(False),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "480p", "generate_audio": True}, "credits": 3.5},
                {"when": {"resolution": "480p"}, "credits": 1.75},
                {"when": {"resolution": "720p", "generate_audio": True}, "credits": 7},
                {"when": {"resolution": "720p"}, "credits": 3.5},
                {"when": {"resolution": "1080p", "generate_audio": True}, "credits": 15},
                {"when": {"resolution": "1080p"}, "credits": 7.5},
            ],
            "default": 3.5,
            "note": "кр/сек без/со звуком: 480p 1.75/3.5, 720p 3.5/7, 1080p 7.5/15",
        },
    },
    {
        "id": "kling-3-0",
        "label": "Kling 3.0",
        "category": "video",
        "api": "jobs",
        "model": "kling-3.0/video",
        "result": "video",
        "desc": "Kling 3.0: std/pro/4K, мульти-шоты, первый/последний кадр, 3–15 сек.",
        "fields": [
            _prompt(),
            _f("mode", "Режим", "select", options=["std", "pro", "4K"], default="std",
               desc="std=720p, pro=1080p, 4K=2160p"),
            _aspect(_ASPECT_VIDEO, "16:9"),
            _duration(5, 3, 15),
            _f("sound", "Звуковые эффекты", "toggle", default=False),
            _f("image_urls", "Первый/последний кадр", "images", max_items=2),
            _f("multi_shots", "Мульти-шоты", "toggle", default=False,
               desc="Несколько планов в одном ролике (промпт разбей по планам)"),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"mode": "std", "sound": True}, "credits": 20},
                {"when": {"mode": "std"}, "credits": 14},
                {"when": {"mode": "pro", "sound": True}, "credits": 27},
                {"when": {"mode": "pro"}, "credits": 18},
                {"when": {"mode": "4K"}, "credits": 67},
            ],
            "default": 14,
            "note": "кр/сек: std 14/20, pro 18/27, 4K 67 (без/со звуком)",
        },
    },
    {
        "id": "kling-v3-turbo-t2v",
        "label": "Kling 3.0 Turbo (текст)",
        "category": "video",
        "api": "jobs",
        "model": "kling/v3-turbo-text-to-video",
        "result": "video",
        "desc": "Быстрый Kling 3.0 из текста, 3–15 сек.",
        "fields": [
            _prompt(),
            _duration(5, 3, 15),
            _aspect(_ASPECT_VIDEO, "16:9"),
            _res(["720p", "1080p"], "720p"),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "720p"}, "credits": 18},
                {"when": {"resolution": "1080p"}, "credits": 22.5},
            ],
            "default": 18,
            "note": "кр/сек: 720p 18, 1080p 22.5",
        },
    },
    {
        "id": "kling-v3-turbo-i2v",
        "label": "Kling 3.0 Turbo (картинка)",
        "category": "video",
        "api": "jobs",
        "model": "kling/v3-turbo-image-to-video",
        "result": "video",
        "desc": "Быстрый Kling 3.0 из стартового кадра.",
        "fields": [
            _prompt(),
            _f("image_urls", "Стартовый кадр", "images", max_items=1, required=True),
            _duration(5, 3, 15),
            _res(["720p", "1080p"], "720p"),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "720p"}, "credits": 18},
                {"when": {"resolution": "1080p"}, "credits": 22.5},
            ],
            "default": 18,
            "note": "кр/сек: 720p 18, 1080p 22.5",
        },
    },
    {
        "id": "kling-3-0-omni-t2v",
        "label": "Kling 3.0 Omni (текст)",
        "category": "video",
        "api": "jobs",
        "model": "kling-3.0-omni/text-to-video",
        "result": "video",
        "desc": "Omni: нативный звук, мульти-шоты, 3–15 сек, до 4K.",
        "fields": [
            _prompt(),
            _f("audio", "Нативный звук", "toggle", default=False),
            _res(["720p", "1080p", "4k"], "720p"),
            _aspect(_ASPECT_VIDEO, "16:9"),
            _duration(5, 3, 15),
            _f("customize_multi_shots", "Свои мульти-шоты", "toggle", default=False),
            _f("prefer_multi_shots", "Умное планирование шотов", "toggle", default=False),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "720p", "audio": True}, "credits": 18},
                {"when": {"resolution": "720p"}, "credits": 14},
                {"when": {"resolution": "1080p", "audio": True}, "credits": 23},
                {"when": {"resolution": "1080p"}, "credits": 18},
                {"when": {"resolution": "4k"}, "credits": 67},
            ],
            "default": 14,
            "note": "кр/сек без/со звуком: 720p 14/18, 1080p 18/23, 4K 67",
        },
    },
    {
        "id": "kling-2-6-t2v",
        "label": "Kling 2.6 (текст)",
        "category": "video",
        "api": "jobs",
        "model": "kling-2.6/text-to-video",
        "result": "video",
        "desc": "Kling 2.6 HD из текста, 5/10 сек.",
        "fields": [
            _prompt(),
            _aspect(_ASPECT_VIDEO, "9:16"),
            _f("duration", "Длительность, сек", "select", options=["5", "10"], default="5"),
            _f("sound", "Звук", "toggle", default=False),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"duration": "5", "sound": True}, "credits": 110},
                {"when": {"duration": "5"}, "credits": 55},
                {"when": {"duration": "10", "sound": True}, "credits": 220},
                {"when": {"duration": "10"}, "credits": 110},
            ],
            "default": 55,
            "note": "за ролик: 5с 55/110, 10с 110/220 кр (без/со звуком)",
        },
    },
    {
        "id": "kling-2-6-i2v",
        "label": "Kling 2.6 (картинка)",
        "category": "video",
        "api": "jobs",
        "model": "kling-2.6/image-to-video",
        "result": "video",
        "desc": "Kling 2.6 HD из стартового кадра, 5/10 сек.",
        "fields": [
            _prompt(),
            _f("image_urls", "Стартовый кадр", "images", max_items=1, required=True),
            _f("duration", "Длительность, сек", "select", options=["5", "10"], default="5"),
            _f("sound", "Звук", "toggle", default=False),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"duration": "5", "sound": True}, "credits": 110},
                {"when": {"duration": "5"}, "credits": 55},
                {"when": {"duration": "10", "sound": True}, "credits": 220},
                {"when": {"duration": "10"}, "credits": 110},
            ],
            "default": 55,
            "note": "за ролик: 5с 55/110, 10с 110/220 кр (без/со звуком)",
        },
    },
    {
        "id": "kling-3-0-motion-control",
        "hint": "Картинка персонажа + видео с движением → персонаж повторяет движение из видео.",
        "label": "Kling 3.0 Motion Control",
        "category": "video",
        "api": "jobs",
        "model": "kling-3.0/motion-control",
        "result": "video",
        "desc": "Перенос движения из референс-видео на картинку.",
        "fields": [
            _prompt("Промпт (опционально)", required=False),
            _f("image_urls", "Картинка-персонаж", "images", max_items=1, required=True),
            _f("video_urls", "Видео-донор движения", "videos", max_items=1, required=True),
            _res(["720p", "1080p"], "720p"),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "720p"}, "credits": 20},
                {"when": {"resolution": "1080p"}, "credits": 27},
            ],
            "default": 20,
            "note": "кр/сек: 720p 20, 1080p 27",
        },
    },
    {
        "id": "grok-imagine-t2v",
        "label": "Grok Imagine (текст)",
        "category": "video",
        "api": "jobs",
        "model": "grok-imagine/text-to-video",
        "result": "video",
        "desc": "xAI Grok Imagine: 6–30 сек, режимы fun/normal/spicy.",
        "fields": [
            _prompt(),
            _aspect(["2:3", "3:2", "1:1", "16:9", "9:16"], "2:3"),
            _f("mode", "Режим", "select", options=["normal", "fun", "spicy"], default="normal"),
            _duration(6, 6, 30),
            _res(["480p", "720p", "1080p"], "720p"),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "480p"}, "credits": 2.4},
                {"when": {"resolution": "720p"}, "credits": 4.5},
                {"when": {"resolution": "1080p"}, "credits": 8},
            ],
            "default": 4.5,
            "note": "кр/сек: 480p 2.4, 720p 4.5, 1080p 8",
        },
    },
    {
        "id": "grok-imagine-i2v",
        "label": "Grok Imagine (картинка)",
        "category": "video",
        "api": "jobs",
        "model": "grok-imagine/image-to-video",
        "result": "video",
        "desc": "Оживление картинки Grok Imagine, 6–30 сек.",
        "fields": [
            _prompt(),
            _f("image_urls", "Стартовый кадр", "images", max_items=1, required=True),
            _f("mode", "Режим", "select", options=["normal", "fun", "spicy"], default="normal"),
            _duration(6, 6, 30),
            _res(["480p", "720p", "1080p"], "720p"),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "480p"}, "credits": 2.4},
                {"when": {"resolution": "720p"}, "credits": 4.5},
                {"when": {"resolution": "1080p"}, "credits": 8},
            ],
            "default": 4.5,
            "note": "кр/сек: 480p 2.4, 720p 4.5, 1080p 8",
        },
    },
    {
        "id": "hailuo-2-3-i2v",
        "label": "Hailuo 2.3 (картинка)",
        "category": "video",
        "api": "jobs",
        "model": "hailuo/2-3-image-to-video-pro",
        "result": "video",
        "desc": "MiniMax Hailuo 2.3: оживление картинки, Standard/Pro, 6/10 сек.",
        "fields": [
            _prompt(),
            _f("image_url", "Стартовый кадр", "images", max_items=1, required=True),
            _f("mode", "Тир", "select", options=["standard", "pro"], default="standard"),
            _f("duration", "Длительность, сек", "select", options=["6", "10"], default="6"),
            _res(["768P", "1080P"], "768P"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"mode": "standard", "duration": "6", "resolution": "768P"}, "credits": 30},
                {"when": {"mode": "standard", "duration": "10", "resolution": "768P"}, "credits": 50},
                {"when": {"mode": "standard", "duration": "6", "resolution": "1080P"}, "credits": 50},
                {"when": {"mode": "pro", "duration": "6", "resolution": "768P"}, "credits": 45},
                {"when": {"mode": "pro", "duration": "10", "resolution": "768P"}, "credits": 90},
                {"when": {"mode": "pro", "duration": "6", "resolution": "1080P"}, "credits": 80},
            ],
            "default": 30,
            "note": "за ролик: std 30/50/50, pro 45/90/80 кр (6с 768 / 10с 768 / 6с 1080)",
        },
    },
    {
        "id": "wan-2-7-t2v",
        "label": "WAN 2.7 (текст)",
        "category": "video",
        "api": "jobs",
        "model": "wan/2-7-text-to-video",
        "result": "video",
        "desc": "WAN 2.7: негатив-промпт, своё аудио, seed, 2–15 сек.",
        "fields": [
            _prompt(),
            _f("negative_prompt", "Негатив-промпт", "textarea"),
            _f("audio_url", "Своё аудио", "audios", max_items=1),
            _res(["720p", "1080p"], "1080p"),
            _f("ratio", "Соотношение сторон", "select",
               options=["16:9", "9:16", "1:1", "4:3", "3:4"], default="16:9"),
            _duration(5, 2, 15),
            _f("prompt_extend", "Умное расширение промпта", "toggle", default=True),
            _f("watermark", "Вотермарка AI", "toggle", default=False),
            _seed(),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "720p"}, "credits": 16},
                {"when": {"resolution": "1080p"}, "credits": 24},
            ],
            "default": 24,
            "note": "кр/сек: 720p 16, 1080p 24",
        },
    },
    {
        "id": "wan-2-7-i2v",
        "label": "WAN 2.7 (картинка)",
        "category": "video",
        "api": "jobs",
        "model": "wan/2-7-image-to-video",
        "result": "video",
        "desc": "WAN 2.7 из картинки, 2–15 сек.",
        "fields": [
            _prompt(),
            _f("image_url", "Стартовый кадр", "images", max_items=1, required=True),
            _f("negative_prompt", "Негатив-промпт", "textarea"),
            _f("audio_url", "Своё аудио", "audios", max_items=1),
            _res(["720p", "1080p"], "1080p"),
            _f("ratio", "Соотношение сторон", "select",
               options=["16:9", "9:16", "1:1", "4:3", "3:4"], default="16:9"),
            _duration(5, 2, 15),
            _f("prompt_extend", "Умное расширение промпта", "toggle", default=True),
            _seed(),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "720p"}, "credits": 16},
                {"when": {"resolution": "1080p"}, "credits": 24},
            ],
            "default": 24,
            "note": "кр/сек: 720p 16, 1080p 24",
        },
    },
    {
        "id": "wan-2-6-t2v",
        "label": "WAN 2.6 (текст)",
        "category": "video",
        "api": "jobs",
        "model": "wan/2-6-text-to-video",
        "result": "video",
        "desc": "WAN 2.6: флэт-цена за 5/10/15 сек.",
        "fields": [
            _prompt(),
            _f("duration", "Длительность, сек", "select", options=["5", "10", "15"], default="5"),
            _res(["720p", "1080p"], "720p"),
            _aspect(_ASPECT_VIDEO, "16:9"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"duration": "5", "resolution": "720p"}, "credits": 70},
                {"when": {"duration": "10", "resolution": "720p"}, "credits": 140},
                {"when": {"duration": "15", "resolution": "720p"}, "credits": 209.5},
                {"when": {"duration": "5", "resolution": "1080p"}, "credits": 104.5},
                {"when": {"duration": "10", "resolution": "1080p"}, "credits": 209.5},
                {"when": {"duration": "15", "resolution": "1080p"}, "credits": 315},
            ],
            "default": 70,
            "note": "за ролик 5/10/15с: 720p 70/140/209.5, 1080p 104.5/209.5/315 кр",
        },
    },
    {
        "id": "pixverse-v6-t2v",
        "label": "PixVerse V6 (текст)",
        "category": "video",
        "api": "jobs",
        "model": "pixverse-v6/text-to-video",
        "result": "video",
        "desc": "PixVerse V6: 1–15 сек, 8 соотношений, мульти-клип.",
        "fields": [
            _prompt(),
            _aspect(["16:9", "4:3", "1:1", "3:4", "9:16", "2:3", "3:2", "21:9"], "16:9"),
            _f("quality", "Разрешение", "select", options=["360p", "540p", "720p", "1080p"], default="720p"),
            _duration(5, 1, 15),
            _f("generate_audio_switch", "Звук", "toggle", default=False),
            _f("generate_multi_clip_switch", "Мульти-клип", "toggle", default=False),
            _seed(),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"quality": "360p", "generate_audio_switch": True}, "credits": 5.6},
                {"when": {"quality": "360p"}, "credits": 4},
                {"when": {"quality": "540p", "generate_audio_switch": True}, "credits": 7.2},
                {"when": {"quality": "540p"}, "credits": 5.6},
                {"when": {"quality": "720p", "generate_audio_switch": True}, "credits": 9.6},
                {"when": {"quality": "720p"}, "credits": 7.2},
                {"when": {"quality": "1080p", "generate_audio_switch": True}, "credits": 18.4},
                {"when": {"quality": "1080p"}, "credits": 14.4},
            ],
            "default": 7.2,
            "note": "кр/сек без/со звуком: 360p 4/5.6, 540p 5.6/7.2, 720p 7.2/9.6, 1080p 14.4/18.4",
        },
    },
    {
        "id": "pixverse-v6-i2v",
        "label": "PixVerse V6 (картинка)",
        "category": "video",
        "api": "jobs",
        "model": "pixverse-v6/image-to-video",
        "result": "video",
        "desc": "PixVerse V6 из стартового кадра.",
        "fields": [
            _prompt(),
            _f("image_url", "Стартовый кадр", "images", max_items=1, required=True),
            _f("quality", "Разрешение", "select", options=["360p", "540p", "720p", "1080p"], default="720p"),
            _duration(5, 1, 15),
            _f("generate_audio_switch", "Звук", "toggle", default=False),
            _seed(),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"quality": "360p", "generate_audio_switch": True}, "credits": 5.6},
                {"when": {"quality": "360p"}, "credits": 4},
                {"when": {"quality": "540p", "generate_audio_switch": True}, "credits": 7.2},
                {"when": {"quality": "540p"}, "credits": 5.6},
                {"when": {"quality": "720p", "generate_audio_switch": True}, "credits": 9.6},
                {"when": {"quality": "720p"}, "credits": 7.2},
                {"when": {"quality": "1080p", "generate_audio_switch": True}, "credits": 18.4},
                {"when": {"quality": "1080p"}, "credits": 14.4},
            ],
            "default": 7.2,
            "note": "кр/сек без/со звуком: 360p 4/5.6 … 1080p 14.4/18.4",
        },
    },
    {
        "id": "minimax-h3-t2v",
        "label": "MiniMax H3 (текст)",
        "category": "video",
        "api": "jobs",
        "model": "minimax-h3/text-to-video",
        "result": "video",
        "desc": "MiniMax H3: 4–15 сек, до 2K.",
        "fields": [
            _prompt(),
            _aspect(["21:9", "16:9", "4:3", "1:1", "3:4", "9:16"], "16:9"),
            _duration(6, 4, 15),
            _res(["768P", "2K"], "2K"),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "768P"}, "credits": 16},
                {"when": {"resolution": "2K"}, "credits": 26},
            ],
            "default": 26,
            "note": "кр/сек: 768P 16, 2K 26; входные картинки свыше 5 — по 8 кр",
        },
    },
    {
        "id": "runway-gen4",
        "label": "Runway",
        "category": "video",
        "api": "runway",
        "endpoint": "/api/v1/runway/generate",
        "result": "video",
        "desc": "Runway: 5/10 сек, 720p/1080p.",
        "fields": [
            _prompt(),
            _f("imageUrl", "Стартовый кадр", "images", max_items=1),
            _f("duration", "Длительность, сек", "select", options=["5", "10"], default="5"),
            _f("quality", "Разрешение", "select", options=["720p", "1080p"], default="720p"),
            _f("aspectRatio", "Соотношение сторон", "select",
               options=["16:9", "9:16"], default="16:9"),
            _f("waterMark", "Вотермарка", "toggle", default=False),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"duration": "5", "quality": "720p"}, "credits": 12},
                {"when": {"duration": "10", "quality": "720p"}, "credits": 30},
                {"when": {"duration": "5", "quality": "1080p"}, "credits": 30},
                {"when": {"duration": "10", "quality": "1080p"}, "credits": 30},
            ],
            "default": 12,
            "note": "за ролик: 5с 720p 12 кр; 10с 720p или 5с 1080p — 30 кр",
        },
    },
    {
        "id": "happyhorse-1-1-t2v",
        "label": "HappyHorse 1.1 (текст)",
        "category": "video",
        "api": "jobs",
        "model": "happyhorse-1-1/text-to-video",
        "result": "video",
        "desc": "HappyHorse 1.1 из текста.",
        "fields": [
            _prompt(),
            _res(["720p", "1080p"], "720p"),
            _aspect(_ASPECT_VIDEO, "16:9"),
            _duration(5, 2, 15),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "720p"}, "credits": 22.5},
                {"when": {"resolution": "1080p"}, "credits": 29},
            ],
            "default": 22.5,
            "note": "кр/сек: 720p 22.5, 1080p 29",
        },
    },
    {
        "id": "infinitalk",
        "hint": "Фото лица + аудио речи → говорящее видео (губы в такт).",
        "label": "InfiniTalk (говорящее фото)",
        "category": "video",
        "api": "jobs",
        "model": "infinitalk/from-audio",
        "result": "video",
        "desc": "Оживляет фото под аудио (липсинк), до 15 сек.",
        "fields": [
            _prompt(),
            _f("image_url", "Фото лица", "images", max_items=1, required=True),
            _f("audio_url", "Аудио речи", "audios", max_items=1, required=True),
            _res(["480p", "720p"], "720p"),
            _seed(),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "480p"}, "credits": 3},
                {"when": {"resolution": "720p"}, "credits": 12},
            ],
            "default": 12,
            "note": "кр/сек: 480p 3, 720p 12; до 15 сек",
        },
    },
    {
        "id": "kling-ai-avatar",
        "hint": "Фото + аудио → говорящий аватар. Pro = 1080p.",
        "label": "Kling AI Avatar",
        "category": "video",
        "api": "jobs",
        "model": "kling/ai-avatar-standard",
        "result": "video",
        "desc": "Говорящий аватар из фото + аудио, до 15 сек.",
        "fields": [
            _prompt("Промпт", required=False),
            _f("image_url", "Фото", "images", max_items=1, required=True),
            _f("audio_url", "Аудио", "audios", max_items=1, required=True),
            _f("mode", "Тир", "select", options=["standard", "pro"], default="standard"),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"mode": "standard"}, "credits": 8},
                {"when": {"mode": "pro"}, "credits": 16},
            ],
            "default": 8,
            "note": "кр/сек: standard 8 (720p), pro 16 (1080p)",
        },
        "model_map": {"standard": "kling/ai-avatar-standard", "pro": "kling/ai-avatar-pro"},
    },
    {
        "id": "omnihuman-1-5",
        "hint": "Фото + аудио → реалистичный говорящий человек (ByteDance).",
        "label": "OmniHuman 1.5",
        "category": "video",
        "api": "jobs",
        "model": "omnihuman-1-5",
        "result": "video",
        "desc": "ByteDance OmniHuman: реалистичный говорящий человек из фото+аудио.",
        "fields": [
            _prompt("Промпт", required=False),
            _f("image_url", "Фото", "images", max_items=1, required=True),
            _f("audio_url", "Аудио", "audios", max_items=1, required=True),
            _f("mask_url", "Маска (опционально)", "images", max_items=1),
            _f("output_resolution", "Разрешение", "select", options=["720p", "1080p"], default="720p"),
            _f("pe_fast_mode", "Быстрый режим", "toggle", default=False),
            _seed(),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [],
            "default": 27,
            "note": "27 кр/сек (~$0.135)",
        },
    },
    {
        "id": "wan-speech-to-video",
        "hint": "Картинка/текст + аудио речи → видео, синхронное с речью.",
        "label": "WAN 2.2 Speech-to-Video",
        "category": "video",
        "api": "jobs",
        "model": "wan/2-2-a14b-speech-to-video-turbo",
        "result": "video",
        "desc": "WAN: видео из текста/картинки под речь (turbo).",
        "fields": [
            _prompt(),
            _f("image_url", "Стартовый кадр", "images", max_items=1),
            _f("audio_url", "Аудио речи", "audios", max_items=1, required=True),
            _res(["480p", "580p", "720p"], "720p"),
            _aspect(_ASPECT_VIDEO, "16:9"),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"resolution": "480p"}, "credits": 12},
                {"when": {"resolution": "580p"}, "credits": 18},
                {"when": {"resolution": "720p"}, "credits": 24},
            ],
            "default": 24,
            "note": "кр/сек: 480p 12, 580p 18, 720p 24",
        },
    },
    {
        "id": "volcengine-lip-sync",
        "hint": "Готовое видео + новое аудио → губы двигаются под новое аудио.",
        "label": "Lip Sync (video→video)",
        "category": "video",
        "api": "jobs",
        "model": "volcengine/video-to-video-lip-sync",
        "result": "video",
        "desc": "Переозвучка готового видео: липсинк под новое аудио.",
        "fields": [
            _f("video_url", "Видео", "videos", max_items=1, required=True),
            _f("audio_url", "Новое аудио", "audios", max_items=1, required=True),
            _f("mode", "Режим", "select", options=["lite", "pro"], default="lite"),
            _f("separate_vocal", "Отделить вокал", "toggle", default=False),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [],
            "default": 8,
            "note": "8 кр/сек (~$0.04)",
        },
    },
    # ============================ КАРТИНКИ ============================
    {
        "id": "gpt-image-2",
        "label": "GPT Image 2",
        "category": "image",
        "api": "jobs",
        "model": "gpt-image-2-text-to-image",
        "model_map_i2i": "gpt-image-2-image-to-image",
        "result": "image",
        "desc": "OpenAI GPT Image 2: фотореализм, текст на картинке, до 4K.",
        "fields": [
            _prompt(),
            _f("image_urls", "Референсы (i2i)", "images", max_items=4),
            _aspect(_ASPECT_IMG, "auto"),
            _res(_RES_IMG, "1K"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"resolution": "1K"}, "credits": 6},
                {"when": {"resolution": "2K"}, "credits": 10},
                {"when": {"resolution": "4K"}, "credits": 16},
            ],
            "default": 6,
            "note": "за картинку: 1K 6, 2K 10, 4K 16 кр",
        },
    },
    {
        "id": "gpt-image-1-5",
        "label": "GPT Image 1.5",
        "category": "image",
        "api": "jobs",
        "model": "gpt-image/1.5-text-to-image",
        "model_map_i2i": "gpt-image/1.5-image-to-image",
        "result": "image",
        "desc": "GPT Image 1.5: дешёвый medium, дорогой high.",
        "fields": [
            _prompt(),
            _f("image_urls", "Референсы (i2i)", "images", max_items=4),
            _aspect(_ASPECT_IMG, "auto"),
            _f("quality", "Качество", "select", options=["medium", "high"], default="medium"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"quality": "medium"}, "credits": 4},
                {"when": {"quality": "high"}, "credits": 22},
            ],
            "default": 4,
            "note": "за картинку: medium 4, high 22 кр",
        },
    },
    {
        "id": "nano-banana-2",
        "label": "Nano Banana 2",
        "category": "image",
        "api": "jobs",
        "model": "nano-banana-2",
        "result": "image",
        "desc": "Google Gemini 3.1 Flash Image: быстро, точный текст, до 14 референсов.",
        "fields": [
            _prompt(),
            _f("image_input", "Референсы (до 14)", "images", max_items=14),
            _aspect(["auto", "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"], "auto"),
            _res(_RES_IMG, "1K"),
            _f("output_format", "Формат", "select", options=["jpg", "png"], default="jpg"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"resolution": "1K"}, "credits": 8},
                {"when": {"resolution": "2K"}, "credits": 12},
                {"when": {"resolution": "4K"}, "credits": 18},
            ],
            "default": 8,
            "note": "за картинку: 1K 8, 2K 12, 4K 18 кр",
        },
    },
    {
        "id": "nano-banana-2-lite",
        "label": "Nano Banana 2 Lite",
        "category": "image",
        "api": "jobs",
        "model": "nano-banana-2-lite",
        "result": "image",
        "desc": "Самый дешёвый Nano Banana — 1K черновики.",
        "fields": [
            _prompt(),
            _f("image_input", "Референсы", "images", max_items=8),
            _aspect(["auto", "1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9"], "auto"),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 4, "note": "4 кр/картинку (1K)"},
    },
    {
        "id": "nano-banana-pro",
        "label": "Nano Banana Pro",
        "category": "image",
        "api": "jobs",
        "model": "nano-banana-pro",
        "result": "image",
        "desc": "Топовая картинка Google: 2K/4K, консистентность персонажей.",
        "fields": [
            _prompt(),
            _f("image_input", "Референсы", "images", max_items=8),
            _aspect(["auto", "1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"], "auto"),
            _res(_RES_IMG, "2K"),
            _f("output_format", "Формат", "select", options=["jpg", "png"], default="jpg"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"resolution": "1K"}, "credits": 18},
                {"when": {"resolution": "2K"}, "credits": 18},
                {"when": {"resolution": "4K"}, "credits": 24},
            ],
            "default": 18,
            "note": "за картинку: 1K/2K 18, 4K 24 кр",
        },
    },
    {
        "id": "nano-banana",
        "label": "Nano Banana",
        "category": "image",
        "api": "jobs",
        "model": "google/nano-banana",
        "model_map_i2i": "google/nano-banana-edit",
        "result": "image",
        "desc": "Классический Nano Banana — дёшево и точно.",
        "fields": [
            _prompt(),
            _f("image_urls", "Референсы (edit)", "images", max_items=4),
            _aspect(["auto", "1:1", "3:2", "2:3", "3:4", "4:3", "9:16", "16:9"], "auto"),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 4, "note": "4 кр/картинку"},
    },
    {
        "id": "seedream-5-pro",
        "label": "Seedream 5 Pro",
        "category": "image",
        "api": "jobs",
        "model": "seedream/5-pro-text-to-image",
        "model_map_i2i": "seedream/5-pro-image-to-image",
        "result": "image",
        "desc": "ByteDance Seedream 5 Pro: контроль, инфографика, портреты.",
        "fields": [
            _prompt(),
            _f("image_urls", "Референсы (i2i)", "images", max_items=6),
            _aspect(["1:1", "4:3", "3:4", "16:9", "9:16", "2:3", "3:2", "21:9"], "1:1"),
            _f("quality", "Качество", "select", options=["basic", "high"], default="basic",
               desc="basic=1K, high=2K"),
            _f("output_format", "Формат", "select", options=["png", "jpeg"], default="png"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"quality": "basic"}, "credits": 7},
                {"when": {"quality": "high"}, "credits": 14},
            ],
            "default": 7,
            "note": "за картинку: 1K 7, 2K 14 кр; входные сверх первой +0.5 кр",
        },
    },
    {
        "id": "seedream-5-lite",
        "label": "Seedream 5 Lite",
        "category": "image",
        "api": "jobs",
        "model": "seedream/5-lite-text-to-image",
        "model_map_i2i": "seedream/5-lite-image-to-image",
        "result": "image",
        "desc": "Лёгкий Seedream 5, до 3K.",
        "fields": [
            _prompt(),
            _f("image_urls", "Референсы (i2i)", "images", max_items=6),
            _aspect(["1:1", "4:3", "3:4", "16:9", "9:16", "2:3", "3:2"], "1:1"),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 5.5, "note": "5.5 кр/картинку"},
    },
    {
        "id": "seedream-4-5",
        "label": "Seedream 4.5",
        "category": "image",
        "api": "jobs",
        "model": "seedream/4.5-text-to-image",
        "model_map_i2i": "seedream/4.5-edit",
        "result": "image",
        "desc": "Seedream 4.5 4K.",
        "fields": [
            _prompt(),
            _f("image_urls", "Референсы (edit)", "images", max_items=6),
            _aspect(["1:1", "4:3", "3:4", "16:9", "9:16", "2:3", "3:2"], "1:1"),
            _f("quality", "Разрешение", "select", options=["2K", "4K"], default="2K"),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 6.5, "note": "6.5 кр/картинку"},
    },
    {
        "id": "flux-2-pro",
        "label": "Flux 2 Pro",
        "category": "image",
        "api": "jobs",
        "model": "flux-2/pro-text-to-image",
        "model_map_i2i": "flux-2/pro-image-to-image",
        "result": "image",
        "desc": "Black Forest Flux 2 Pro: до 8 референсов бесплатно.",
        "fields": [
            _prompt(),
            _f("image_urls", "Референсы (до 8)", "images", max_items=8),
            _aspect(["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"], "1:1"),
            _res(["1K", "2K"], "1K"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"resolution": "1K"}, "credits": 5},
                {"when": {"resolution": "2K"}, "credits": 7},
            ],
            "default": 5,
            "note": "за картинку: 1K 5, 2K 7 кр",
        },
    },
    {
        "id": "flux-2-flex",
        "label": "Flux 2 Flex",
        "category": "image",
        "api": "jobs",
        "model": "flux-2/flex-text-to-image",
        "model_map_i2i": "flux-2/flex-image-to-image",
        "result": "image",
        "desc": "Flux 2 Flex — тонкий контроль, дороже Pro.",
        "fields": [
            _prompt(),
            _f("image_urls", "Референсы (до 8)", "images", max_items=8),
            _aspect(["1:1", "4:3", "3:4", "16:9", "9:16", "3:2", "2:3"], "1:1"),
            _res(["1K", "2K"], "1K"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"resolution": "1K"}, "credits": 14},
                {"when": {"resolution": "2K"}, "credits": 24},
            ],
            "default": 14,
            "note": "за картинку: 1K 14, 2K 24 кр",
        },
    },
    {
        "id": "imagen4",
        "label": "Imagen 4",
        "category": "image",
        "api": "jobs",
        "model": "google/imagen4",
        "result": "image",
        "desc": "Google Imagen 4: fast/standard/ultra.",
        "fields": [
            _prompt(),
            _f("variant", "Вариант", "select", options=["fast", "standard", "ultra"], default="standard"),
            _f("negative_prompt", "Негатив-промпт", "textarea"),
            _aspect(["1:1", "16:9", "9:16", "3:4", "4:3", "auto"], "1:1"),
            _seed(),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"variant": "fast"}, "credits": 4},
                {"when": {"variant": "standard"}, "credits": 8},
                {"when": {"variant": "ultra"}, "credits": 12},
            ],
            "default": 8,
            "note": "за картинку: fast 4, standard 8, ultra 12 кр",
        },
        "model_map": {
            "fast": "google/imagen4-fast",
            "standard": "google/imagen4",
            "ultra": "google/imagen4-ultra",
        },
    },
    {
        "id": "grok-imagine-image-2",
        "label": "Grok Imagine Image 2",
        "category": "image",
        "api": "jobs",
        "model": "grok-imagine-image-2-0/text-to-image",
        "model_map_i2i": "grok-imagine-image-2-0/image-edit",
        "result": "image",
        "desc": "xAI картинки: дёшево, 2 изображения за генерацию.",
        "fields": [
            _prompt(),
            _f("image_urls", "Референсы (edit)", "images", max_items=4),
            _aspect(["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"], "1:1"),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 4, "note": "4 кр/генерацию (2 картинки)"},
    },
    {
        "id": "qwen3-image",
        "label": "Qwen Image 3",
        "category": "image",
        "api": "jobs",
        "model": "qwen3/text-to-image",
        "model_map_i2i": "qwen3/image-to-image",
        "result": "image",
        "desc": "Qwen Image 3: силён в тексте на картинке, мультиязычие.",
        "fields": [
            _prompt(),
            _f("image_urls", "Референсы (i2i)", "images", max_items=4),
            _aspect(["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"], "1:1"),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 4.8, "note": "4.8 кр/картинку (1K/2K)"},
    },
    {
        "id": "z-image",
        "label": "Z-Image",
        "category": "image",
        "api": "jobs",
        "model": "z-image",
        "result": "image",
        "desc": "Самая дешёвая картинка на kie — черновики копейки.",
        "fields": [
            _prompt(),
            _aspect(["1:1", "16:9", "9:16", "4:3", "3:4"], "1:1"),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 0.8, "note": "0.8 кр/картинку (~$0.004)"},
    },
    {
        "id": "ideogram-v3",
        "label": "Ideogram V3",
        "category": "image",
        "api": "jobs",
        "model": "ideogram/v3-text-to-image",
        "model_map_i2i": "ideogram/v3-edit",
        "result": "image",
        "desc": "Ideogram V3: лучший текст/логотипы на картинке.",
        "fields": [
            _prompt(),
            _f("image_urls", "Референсы (edit)", "images", max_items=4),
            _f("rendering_speed", "Скорость/качество", "select",
               options=["TURBO", "BALANCED", "QUALITY"], default="TURBO"),
            _aspect(["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"], "1:1"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"rendering_speed": "TURBO"}, "credits": 3.5},
                {"when": {"rendering_speed": "BALANCED"}, "credits": 7},
                {"when": {"rendering_speed": "QUALITY"}, "credits": 10},
            ],
            "default": 3.5,
            "note": "за картинку: Turbo 3.5, Balanced 7, Quality 10 кр",
        },
    },
    {
        "id": "4o-image",
        "label": "4o Image",
        "category": "image",
        "api": "jobs",
        "model": "4o-image-api",
        "result": "image",
        "desc": "4o Image: флэт-цена, без настроек качества.",
        "fields": [
            _prompt(),
            _f("image_urls", "Референсы", "images", max_items=4),
            _aspect(["1:1", "3:2", "2:3"], "1:1"),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 6, "note": "6 кр/картинку (~$0.03)"},
    },
    {
        "id": "wan-2-7-image",
        "label": "WAN 2.7 Image",
        "category": "image",
        "api": "jobs",
        "model": "wan/2-7-image",
        "result": "image",
        "desc": "WAN 2.7 картинки: обычная и Pro.",
        "fields": [
            _prompt(),
            _f("variant", "Вариант", "select", options=["standard", "pro"], default="standard"),
            _aspect(["1:1", "16:9", "9:16", "4:3", "3:4"], "1:1"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"variant": "standard"}, "credits": 4.8},
                {"when": {"variant": "pro"}, "credits": 12},
            ],
            "default": 4.8,
            "note": "за картинку: standard 4.8, pro 12 кр",
        },
        "model_map": {"standard": "wan/2-7-image", "pro": "wan/2-7-image-pro"},
    },
    # ============================ МУЗЫКА (SUNO) ============================
    {
        "id": "suno-music",
        "hint": "Опиши трек («мрачный фонк, женский вокал») или в custom-режиме дай свой текст, стиль и название.",
        "label": "Suno — трек",
        "category": "music",
        "api": "suno",
        "endpoint": "/api/v1/generate",
        "result": "audio",
        "desc": "Полный трек по описанию: вокал или инструментал, модели V4…V5_5.",
        "fields": [
            _f("customMode", "Свой режим (стиль/текст/название)", "toggle", default=True),
            _f("model", "Модель", "select",
               options=["V5_5", "V5", "V4_5PLUS", "V4_5", "V4"], default="V5_5"),
            _prompt("Описание / текст песни"),
            _f("style", "Стиль", "text", show_if={"customMode": True},
               desc="например: dark phonk, female vocal, 120 bpm"),
            _f("title", "Название", "text", show_if={"customMode": True}),
            _f("instrumental", "Без вокала", "toggle", default=False),
            _f("negativeTags", "Исключить стили", "text"),
            _f("vocalGender", "Пол вокала", "select", options=["", "m", "f"], default=""),
            _f("styleWeight", "Сила стиля (0–1)", "number", min=0, max=1, step=0.01),
            _f("weirdnessConstraint", "Странность (0–1)", "number", min=0, max=1, step=0.01),
            _f("audioWeight", "Вес аудио-признаков (0–1)", "number", min=0, max=1, step=0.01),
            _f("duration", "Длительность, сек (только V5_5)", "number",
               min=10, max=360, step=1, show_if={"model": "V5_5"}),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 12, "note": "12 кр/трек (~$0.06)"},
    },
    {
        "id": "suno-extend",
        "hint": "Продлевает трек Suno: нужен audioId из прошлой генерации (виден в логах kie).",
        "label": "Suno — продлить трек",
        "category": "music",
        "api": "suno",
        "endpoint": "/api/v1/generate/extend",
        "result": "audio",
        "desc": "Продление существующего трека Suno по audioId.",
        "fields": [
            _f("audioId", "ID трека (audioId)", "text", required=True),
            _f("defaultParamFlag", "Свои параметры", "toggle", default=True),
            _prompt("Промпт продления", required=False),
            _f("style", "Стиль", "text"),
            _f("title", "Название", "text"),
            _f("continueAt", "Продлить с, сек", "number", min=0, step=0.1),
            _f("model", "Модель", "select",
               options=["V5_5", "V5", "V4_5PLUS", "V4_5", "V4"], default="V5_5"),
            _f("instrumental", "Без вокала", "toggle", default=False),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 12, "note": "12 кр/продление"},
    },
    {
        "id": "suno-upload-cover",
        "hint": "Твоё аудио → кавер в новом стиле. Загрузи файл, опиши стиль.",
        "label": "Suno — кавер на своё аудио",
        "category": "music",
        "api": "suno",
        "endpoint": "/api/v1/generate/upload-cover",
        "result": "audio",
        "desc": "Загружаешь аудио — Suno делает кавер в новом стиле.",
        "fields": [
            _f("uploadUrl", "Твое аудио", "audios", max_items=1, required=True),
            _f("customMode", "Свой режим", "toggle", default=True),
            _prompt("Промпт/стиль кавера"),
            _f("style", "Стиль", "text"),
            _f("title", "Название", "text"),
            _f("model", "Модель", "select",
               options=["V5_5", "V5", "V4_5PLUS", "V4_5", "V4"], default="V5_5"),
            _f("instrumental", "Без вокала", "toggle", default=False),
            _f("negativeTags", "Исключить стили", "text"),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 12, "note": "12 кр/кавер"},
    },
    {
        "id": "suno-add-vocals",
        "hint": "Инструментал + текст песни → трек с вокалом.",
        "label": "Suno — добавить вокал",
        "category": "music",
        "api": "suno",
        "endpoint": "/api/v1/generate/add-vocals",
        "result": "audio",
        "desc": "Добавляет вокал к инструментальной дорожке (uploadUrl).",
        "fields": [
            _f("uploadUrl", "Инструментал (аудио)", "audios", max_items=1, required=True),
            _prompt("Текст песни"),
            _f("style", "Стиль", "text"),
            _f("title", "Название", "text"),
            _f("model", "Модель", "select",
               options=["V5_5", "V5", "V4_5PLUS", "V4_5", "V4"], default="V5_5"),
            _f("vocalGender", "Пол вокала", "select", options=["", "m", "f"], default=""),
            _f("negativeTags", "Исключить стили", "text"),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 12, "note": "12 кр/запрос"},
    },
    {
        "id": "suno-separate-vocals",
        "hint": "Трек → вокал и минус раздельно (или по инструментам).",
        "label": "Suno — разделить вокал/минус",
        "category": "music",
        "api": "suno",
        "endpoint": "/api/v1/vocal-removal/generate",
        "result": "audio",
        "desc": "Стем-сепарация: вокал/инструментал или по инструментам.",
        "fields": [
            _f("uploadUrl", "Трек (аудио)", "audios", max_items=1, required=True),
            _f("type", "Тип разделения", "select",
               options=["separate_vocals", "split_stem", "advanced_split"], default="separate_vocals",
               desc="separate_vocals — вокал+минус; split_stem — мультистем; advanced_split — по stemName"),
            _f("stemName", "Инструмент (для advanced)", "text",
               show_if={"type": "advanced_split"},
               desc="например: drums, bass, piano"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"type": "separate_vocals"}, "credits": 10},
                {"when": {"type": "split_stem"}, "credits": 50},
                {"when": {"type": "advanced_split"}, "credits": 20},
            ],
            "default": 10,
            "note": "вокал/минус 10, мультистем 50, по инструменту 20 кр",
        },
    },
    {
        "id": "suno-lyrics",
        "hint": "Опиши тему → готовый текст песни для Suno.",
        "label": "Suno — текст песни",
        "category": "music",
        "api": "suno",
        "endpoint": "/api/v1/lyrics",
        "result": "text",
        "desc": "Генерация текста песни по описанию.",
        "fields": [
            _prompt("О чём песня"),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 0.4, "note": "0.4 кр/текст (~$0.002)"},
    },
    # ============================ ЗВУКИ ============================
    {
        "id": "suno-sounds",
        "hint": "Пиши звук словами: «удар по металлу», «шаги по снегу», «whoosh для перехода». Loop — для бесшовного фона, темп/тональность — под музыку ролика.",
        "label": "Suno — звуковые эффекты",
        "category": "sound",
        "api": "suno",
        "endpoint": "/api/v1/generate/sounds",
        "result": "audio",
        "desc": "Генератор звуков/SFX по описанию: удары, ambience, whoosh…",
        "fields": [
            _prompt("Описание звука", max_len=500),
            _f("model", "Модель", "select", options=["V5_5", "V5"], default="V5_5",
               desc="V5_5 — актуальный генератор SFX; V5 — запасной"),
            _f("soundLoop", "Зациклить", "toggle", default=False),
            _f("soundTempo", "Темп (BPM, пусто — авто)", "number", min=1, max=300, step=1),
            _f("soundKey", "Тональность", "select",
               options=["Any", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
                        "Cm", "C#m", "Dm", "D#m", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "A#m", "Bm"],
               default="Any", omit_values=["Any"]),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 2.5, "note": "2.5 кр/звук (~$0.0125)"},
    },
    # ============================ ГОЛОС / TTS ============================
    {
        "id": "elevenlabs-tts-turbo",
        "hint": "Текст → озвучка. Цена растёт от длины текста (за 1000 символов).",
        "label": "ElevenLabs Turbo 2.5",
        "category": "voice",
        "api": "jobs",
        "model": "elevenlabs/text-to-speech-turbo-2-5",
        "result": "audio",
        "desc": "Быстрый TTS: 6 кр за 1000 символов, скорость/стабильность/стиль.",
        "fields": [
            _f("text", "Текст", "textarea", required=True),
            _f("voice", "Голос (имя или voice_id)", "text", default="Rachel",
               desc="Rachel, Adam, Bella, Elli, Josh, Domi… или voice_id из ElevenLabs"),
            _f("stability", "Стабильность (0–1)", "number", default=0.5, min=0, max=1, step=0.01),
            _f("similarity_boost", "Схожесть (0–1)", "number", default=0.75, min=0, max=1, step=0.01),
            _f("style", "Стиль (0–1)", "number", default=0, min=0, max=1, step=0.01),
            _f("speed", "Скорость (0.7–1.2)", "number", default=1, min=0.7, max=1.2, step=0.01),
            _f("language_code", "Язык (ISO, напр. ru)", "text"),
            _f("timestamps", "Таймстемпы слов", "toggle", default=False),
            _f("previous_text", "Текст до (контекст)", "textarea"),
            _f("next_text", "Текст после (контекст)", "textarea"),
        ],
        "pricing": {
            "unit": "1k_chars",
            "rules": [],
            "default": 6,
            "note": "6 кр за 1000 символов (~$0.03)",
        },
    },
    {
        "id": "elevenlabs-tts-multilingual",
        "hint": "Текст → озвучка качественнее Turbo, дороже. Цена за 1000 символов.",
        "label": "ElevenLabs Multilingual V2",
        "category": "voice",
        "api": "jobs",
        "model": "elevenlabs/text-to-speech-multilingual-v2",
        "result": "audio",
        "desc": "Качественный мультиязычный TTS (русский ок).",
        "fields": [
            _f("text", "Текст", "textarea", required=True),
            _f("voice", "Голос (имя или voice_id)", "text", default="Rachel"),
            _f("stability", "Стабильность (0–1)", "number", default=0.5, min=0, max=1, step=0.01),
            _f("similarity_boost", "Схожесть (0–1)", "number", default=0.75, min=0, max=1, step=0.01),
            _f("style", "Стиль (0–1)", "number", default=0, min=0, max=1, step=0.01),
            _f("speed", "Скорость (0.7–1.2)", "number", default=1, min=0.7, max=1.2, step=0.01),
            _f("language_code", "Язык (ISO, напр. ru)", "text"),
            _f("timestamps", "Таймстемпы слов", "toggle", default=False),
        ],
        "pricing": {
            "unit": "1k_chars",
            "rules": [],
            "default": 12,
            "note": "12 кр за 1000 символов (~$0.06)",
        },
    },
    {
        "id": "elevenlabs-dialogue-v3",
        "hint": "Диалог нескольких голосов: каждая строка = «Голос | реплика». Цена за 1000 символов суммарно.",
        "label": "ElevenLabs Dialogue V3",
        "category": "voice",
        "api": "jobs",
        "model": "elevenlabs/text-to-dialogue-v3",
        "result": "audio",
        "desc": "Мульти-голосовой диалог: каждая реплика своим голосом.",
        "fields": [
            _f("dialogue", "Реплики (голос | текст — по строкам)", "dialogue", required=True,
               desc="Rachel | Привет!\nAdam | И тебе привет."),
            _f("stability", "Стабильность", "select", options=["0", "0.5", "1"], default="0.5"),
            _f("language_code", "Язык (ISO, напр. ru)", "text"),
        ],
        "pricing": {
            "unit": "1k_chars",
            "rules": [],
            "default": 14,
            "note": "14 кр за 1000 символов (~$0.07)",
        },
    },
    {
        "id": "elevenlabs-audio-isolation",
        "hint": "Аудио с шумом → чистая речь. Промпт не нужен.",
        "label": "ElevenLabs — изоляция голоса",
        "category": "voice",
        "api": "jobs",
        "model": "elevenlabs/audio-isolation",
        "result": "audio",
        "desc": "Чистит аудио: убирает фон/шум, оставляет речь.",
        "fields": [
            _f("audio_url", "Аудио", "audios", max_items=1, required=True),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 2, "note": "цена уточняется на kie.ai/pricing"},
    },
    # ============================ АПСКЕЙЛ / УТИЛИТЫ ============================
    {
        "id": "topaz-video-upscale",
        "hint": "Как работает: загружаешь своё видео (например 720p из Seedance) → выбираешь кратность → получаешь то же видео в ×2 (≈2K) или ×4 (≈4K). Цена — за секунду исходника.",
        "label": "Topaz — апскейл видео",
        "category": "tools",
        "api": "jobs",
        "model": "topaz/video-upscale",
        "result": "video",
        "desc": "Topaz Video AI: апскейл ×1/×2/×4 (720p→2K/4K).",
        "fields": [
            _f("video_url", "Видео", "videos", max_items=1, required=True),
            _f("upscale_factor", "Кратность", "select", options=["1", "2", "4"], default="2"),
        ],
        "pricing": {
            "unit": "sec",
            "rules": [
                {"when": {"upscale_factor": "4"}, "credits": 14},
                {"when": {"upscale_factor": "2"}, "credits": 8},
                {"when": {"upscale_factor": "1"}, "credits": 8},
            ],
            "default": 8,
            "note": "кр/сек исходника: ×1/×2 — 8, ×4 — 14",
        },
    },
    {
        "id": "topaz-image-upscale",
        "hint": "Картинка → та же картинка в 2K или 4K. Без промпта — только файл.",
        "label": "Topaz — апскейл картинки",
        "category": "tools",
        "api": "jobs",
        "model": "topaz/image-upscale",
        "result": "image",
        "desc": "Topaz: апскейл картинки до 2K/4K.",
        "fields": [
            _f("image_url", "Картинка", "images", max_items=1, required=True),
            _f("target", "Цель", "select", options=["2K", "4K"], default="2K"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"target": "2K"}, "credits": 10},
                {"when": {"target": "4K"}, "credits": 20},
            ],
            "default": 10,
            "note": "за картинку: ≤2K 10, 4K 20 кр",
        },
    },
    {
        "id": "grok-imagine-upscale",
        "hint": "Видео 480p → 720p или 1080p. Загружаешь файл, выбираешь цель — всё.",
        "label": "Grok — апскейл видео",
        "category": "tools",
        "api": "jobs",
        "model": "grok-imagine/upscale",
        "result": "video",
        "desc": "Апскейл видео Grok: 480p→720p/1080p, 720p→1080p.",
        "fields": [
            _f("video_url", "Видео", "videos", max_items=1, required=True),
            _f("target_resolution", "Цель", "select", options=["720p", "1080p"], default="1080p"),
        ],
        "pricing": {
            "unit": "gen",
            "rules": [
                {"when": {"target_resolution": "720p"}, "credits": 10},
                {"when": {"target_resolution": "1080p"}, "credits": 20},
            ],
            "default": 20,
            "note": "за ролик: →720p 10, →1080p 20 кр (с 480p →1080p 30)",
        },
    },
    {
        "id": "recraft-remove-bg",
        "hint": "Картинка → PNG без фона (прозрачный). Промпт не нужен.",
        "label": "Recraft — убрать фон",
        "category": "tools",
        "api": "jobs",
        "model": "recraft/remove-background",
        "result": "image",
        "desc": "Вырезает объект с прозрачным фоном.",
        "fields": [
            _f("image_url", "Картинка", "images", max_items=1, required=True),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 1, "note": "1 кр/картинку (~$0.005)"},
    },
    {
        "id": "recraft-crisp-upscale",
        "hint": "Картинка → резче и больше. Промпт не нужен.",
        "label": "Recraft — crisp апскейл",
        "category": "tools",
        "api": "jobs",
        "model": "recraft/crisp-upscale",
        "result": "image",
        "desc": "Дешёвая резкость/апскейл картинки.",
        "fields": [
            _f("image_url", "Картинка", "images", max_items=1, required=True),
        ],
        "pricing": {"unit": "gen", "rules": [], "default": 0.5, "note": "0.5 кр/картинку (~$0.0025)"},
    },
]

_BY_ID: dict[str, dict[str, Any]] = {m["id"]: m for m in MODELS}

_FILE_KINDS = {"images", "videos", "audios", "file"}


def get_model(model_id: str) -> dict[str, Any] | None:
    return _BY_ID.get(model_id)


def list_models(category: str | None = None) -> list[dict[str, Any]]:
    if not category:
        return list(MODELS)
    return [m for m in MODELS if m["category"] == category]


def defaults_for(spec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in spec.get("fields") or []:
        if "default" in f:
            out[f["name"]] = f["default"]
    return out


def normalize_values(spec: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Значения с дефолтами каталога (для прайсинга и валидации)."""
    merged = defaults_for(spec)
    for k, v in (values or {}).items():
        merged[k] = v
    return merged


def _has_files(values: dict[str, Any], spec: dict[str, Any], kinds: set[str]) -> bool:
    for f in spec.get("fields") or []:
        if f.get("kind") not in kinds:
            continue
        v = values.get(f["name"])
        if isinstance(v, list) and v:
            return True
        if isinstance(v, str) and v.strip():
            return True
    return False


def _match(when: dict[str, Any], values: dict[str, Any], spec: dict[str, Any]) -> bool:
    for key, want in when.items():
        if key == "_has_images":
            if _has_files(values, spec, {"images"}) != bool(want):
                return False
            continue
        if key == "_has_videos":
            if _has_files(values, spec, {"videos"}) != bool(want):
                return False
            continue
        got = values.get(key)
        if isinstance(want, bool):
            truthy = got is True or str(got).strip().lower() in ("true", "1", "yes", "on")
            if truthy != want:
                return False
            continue
        if str(got) != str(want):
            return False
    return True


def estimate_credits(
    spec: dict[str, Any], values: dict[str, Any]
) -> dict[str, Any]:
    """Динамическая цена: кредиты и $ по текущим настройкам."""
    pricing = spec.get("pricing") or {}
    merged = normalize_values(spec, values)
    credits: float | None = None
    for rule in pricing.get("rules") or []:
        if _match(rule.get("when") or {}, merged, spec):
            credits = float(rule["credits"])
            break
    if credits is None:
        default = pricing.get("default")
        credits = float(default) if default is not None else 0.0
    unit = pricing.get("unit") or "gen"
    total = credits
    multiplier = 1.0
    if unit == "sec":
        try:
            multiplier = float(merged.get("duration") or 5)
        except (TypeError, ValueError):
            multiplier = 5.0
        multiplier = max(1.0, multiplier)
        total = credits * multiplier
    elif unit == "1k_chars":
        text = str(merged.get("text") or merged.get("dialogue") or "")
        multiplier = max(1.0, math.ceil(len(text) / 1000))
        total = credits * multiplier
    return {
        "credits": round(total, 2),
        "usd": round(total * CREDIT_USD, 4),
        "unit": unit,
        "unit_credits": credits,
        "multiplier": multiplier,
        "note": pricing.get("note") or "",
    }


def validate_values(
    spec: dict[str, Any], values: dict[str, Any]
) -> list[str]:
    """Проверки по схеме каталога → список ошибок (пусто = ок)."""
    errors: list[str] = []
    merged = normalize_values(spec, values)
    known = {f["name"] for f in spec.get("fields") or []}
    for f in spec.get("fields") or []:
        name = f["name"]
        v = merged.get(name)
        empty = v is None or (isinstance(v, str) and not v.strip()) or (
            isinstance(v, list) and not v
        )
        if f.get("required") and empty:
            errors.append(f"{f['label'] or name}: обязательное поле")
            continue
        if empty:
            continue
        kind = f.get("kind")
        if kind == "select" and f.get("options"):
            if str(v) not in [str(o) for o in f["options"]]:
                errors.append(f"{name}: значение {v!r} вне списка {f['options']}")
        elif kind == "number":
            try:
                num = float(v)
                if f.get("min") is not None and num < float(f["min"]):
                    errors.append(f"{name}: {num} < min {f['min']}")
                if f.get("max") is not None and num > float(f["max"]):
                    errors.append(f"{name}: {num} > max {f['max']}")
            except (TypeError, ValueError):
                errors.append(f"{name}: не число: {v!r}")
        elif kind in ("text", "textarea") and f.get("max_len"):
            if len(str(v)) > int(f["max_len"]):
                errors.append(f"{name}: {len(str(v))} символов > max {f['max_len']}")
        elif kind in _FILE_KINDS:
            items = v if isinstance(v, list) else [v]
            if f.get("max_items") and len(items) > int(f["max_items"]):
                errors.append(f"{name}: файлов {len(items)} > {f['max_items']}")
            for u in items:
                if not str(u).startswith(("http://", "https://", "asset://")):
                    errors.append(f"{name}: не URL: {str(u)[:60]!r}")
    unknown = set((values or {}).keys()) - known
    if unknown:
        # неизвестные ключи не режем жёстко — вдруг поле каталога отстало
        pass
    return errors


def resolve_model_id(spec: dict[str, Any], values: dict[str, Any]) -> str:
    """model с учётом model_map (variant→другой model id) и i2i-варианта."""
    merged = normalize_values(spec, values)
    mmap = spec.get("model_map") or {}
    for f in spec.get("fields") or []:
        val = merged.get(f["name"])
        if val is not None and str(val) in mmap:
            return mmap[str(val)]
    i2i = spec.get("model_map_i2i")
    if i2i and _has_files(merged, spec, {"images"}):
        return i2i
    return spec.get("model") or ""


def build_payload(spec: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """Тело запроса под тип API: jobs (model+input) или flat (veo/suno/runway)."""
    merged = normalize_values(spec, values)
    body: dict[str, Any] = {}
    for f in spec.get("fields") or []:
        name = f["name"]
        v = merged.get(name)
        if v is None:
            continue
        omit = {str(x) for x in (f.get("omit_values") or [])}
        if str(v) in omit:
            continue
        kind = f.get("kind")
        if kind in _FILE_KINDS:
            items = v if isinstance(v, list) else [v]
            items = [str(u).strip() for u in items if str(u).strip()]
            if not items:
                continue
            # одиночные поля (max_items=1) — строкой, если так в спеке
            if f.get("max_items") == 1 and name.endswith("_url"):
                body[name] = items[0]
            else:
                body[name] = items
            continue
        if kind == "toggle":
            body[name] = v is True or str(v).strip().lower() in ("true", "1", "yes", "on")
            continue
        if kind == "number":
            try:
                num = float(v)
                body[name] = int(num) if float(num).is_integer() else num
            except (TypeError, ValueError):
                pass
            continue
        if kind == "dialogue":
            lines = [ln for ln in str(v).splitlines() if "|" in ln]
            dlg = []
            for ln in lines:
                voice, _, text = ln.partition("|")
                if text.strip():
                    dlg.append({"voice": voice.strip(), "text": text.strip()})
            if dlg:
                body[name] = dlg
            continue
        if isinstance(v, str) and not v.strip():
            continue
        body[name] = v
    api = spec.get("api")
    if api == "jobs":
        return {"model": resolve_model_id(spec, merged), "input": body}
    if api in ("veo", "runway") and "duration" in body:
        # veo/runway ждут duration числом, даже если в UI это select
        with contextlib.suppress(TypeError, ValueError):
            body["duration"] = int(float(body["duration"]))
    return body


def _media_of(m: dict[str, Any]) -> str:
    """В какой тип Create-пикера попадает модель: image | video | audio."""
    cat = m.get("category")
    if cat in ("video",):
        return "video"
    if cat in ("image",):
        return "image"
    if cat in ("music", "sound", "voice"):
        return "audio"
    # tools — по типу результата
    return str(m.get("result") or "video")


def catalog_for_ui() -> dict[str, Any]:
    """Каталог для фронта: категории + модели с полями и правилами цен."""
    return {
        "credit_usd": CREDIT_USD,
        "categories": CATEGORIES,
        "models": [
            {
                "id": m["id"],
                "label": m["label"],
                "category": m["category"],
                "media": _media_of(m),
                "desc": m.get("desc") or "",
                "hint": m.get("hint") or "",
                "result": m.get("result"),
                "fields": m.get("fields") or [],
                "pricing": m.get("pricing") or {},
            }
            for m in MODELS
        ],
    }
