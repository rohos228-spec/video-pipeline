"""Каталог вариантов генерации для мастера настроек проекта.

5 вопросов, которые бот задаёт после создания проекта:
  1. Генератор картинок (7 опций)
  2. Соотношение сторон (8 опций)
  3. Разрешение картинки (2 опции — 2K / 4K)
  4. Видео-генератор (13 опций)
  5. Разрешение видео (2 опции — 720p / 1080p)

Эти настройки:
  • хранятся в Project.image_generator / aspect_ratio / image_resolution /
    video_generator / video_resolution (TEXT колонки)
  • включаются в контекст master-промтов к ChatGPT (чтобы он стилизовал
    промты под возможности конкретного генератора)
  • используются в outsee.py для выбора правильной модели / aspect / 2K/4K
  • пишутся в xlsx (лист General) чтобы пользователь мог их увидеть/поправить

Значения `outsee_slug` — это часть URL `https://outsee.io/image?model=<slug>`
или `https://outsee.io/video?model=<slug>`. Реальный список slug'ов outsee
публично не задокументирован; мы используем «интуитивные» slug'и. Если
какой-то не сработает — его легко поправить здесь, не трогая остальной код.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OptionChoice:
    """Одна кнопка в мастере настроек.

    id          — строковый ID (будет лежать в БД)
    label       — текст на кнопке (видит юзер)
    outsee_slug — slug для URL / для клика в UI outsee
    short_desc  — одна строка пояснений (для GPT-контекста)
    """

    id: str
    label: str
    outsee_slug: str
    short_desc: str = ""


# ---- 1. Генераторы картинок (без Topaz Upscale — это не генератор) ---------

IMAGE_GENERATORS: list[OptionChoice] = [
    OptionChoice(
        "nano_banana_2", "Nano Banana 2", "nano-banana-2",
        "Самая новая версия Nano banana",
    ),
    OptionChoice(
        "nano_banana", "Nano Banana", "nano-banana",
        "Быстрая и точная. Хороша для точечного редактирования ваших фото",
    ),
    OptionChoice(
        "nano_banana_pro", "Nano Banana Pro", "nano-banana-pro",
        "Лучшая модель на рынке (TOP). Идеальна для любых задач",
    ),
    OptionChoice(
        "seedream_4_5", "Seedream 4.5", "seedream-4-5",
        "Продвинутая модель от TikTok. Подходит для всего. 4K",
    ),
    OptionChoice(
        "seedream_5_0_lite", "Seedream 5.0 Lite", "seedream-5-0-lite",
        "Новейшая версия Seedream. Быстрая генерация в высоком качестве",
    ),
    OptionChoice(
        "gpt_image_1_5", "GPT Image 1.5", "gpt-image-1-5",
        "Флагманская модель OpenAI. Универсальна и надёжна",
    ),
    OptionChoice(
        "gpt_image_2", "GPT Image 2", "gpt-image-2",
        "Новейшая модель OpenAI. Идеальна для постеров и рекламы с текстом. До 4K",
    ),
]


# ---- 2. Соотношения сторон -------------------------------------------------

ASPECT_RATIOS: list[OptionChoice] = [
    OptionChoice("1_1", "1:1", "1:1", "Квадрат (Instagram-пост)"),
    OptionChoice("16_9", "16:9", "16:9", "Широкий (YouTube, TV, landscape)"),
    OptionChoice("9_16", "9:16", "9:16", "Вертикаль (Reels, TikTok, Shorts)"),
    OptionChoice("4_3", "4:3", "4:3", "Классика (ТВ старое, iPad)"),
    OptionChoice("3_4", "3:4", "3:4", "Вертикальная классика (портрет)"),
    OptionChoice("2_3", "2:3", "2:3", "Вертикальный (портрет-фото)"),
    OptionChoice("3_2", "3:2", "3:2", "Горизонтальный (DSLR-фото)"),
    OptionChoice("21_9", "21:9", "21:9", "Ультра-широкий (cinematic)"),
]


# ---- 3. Разрешение картинки ------------------------------------------------

IMAGE_RESOLUTIONS: list[OptionChoice] = [
    OptionChoice("2k", "2K", "2K", "2K — стандартное разрешение"),
    OptionChoice("4k", "4K", "4K", "4K — максимальное качество"),
]


# ---- 4. Видео-генераторы (13 штук, без Topaz Video Upscale) ---------------

VIDEO_GENERATORS: list[OptionChoice] = [
    OptionChoice(
        "kling_3", "Kling 3.0", "kling-3-0",
        "Новейшая Kling (TOP). Гибкая длительность, нативное аудио, мультишот",
    ),
    OptionChoice(
        "kling_2_6", "Kling 2.6", "kling-2-6",
        "Лучшее соотношение цена/качество среди Kling-моделей",
    ),
    OptionChoice(
        "kling_2_5_turbo", "Kling 2.5 Turbo", "kling-2-5-turbo",
        "Хороший выбор для генерации по первому-последнему кадру",
    ),
    OptionChoice(
        "kling_lip_sync", "Kling Lip Sync", "kling-lip-sync",
        "Синхронизация губ под аудио",
    ),
    OptionChoice(
        "kling_motion_2_6", "Kling Motion Control 2.6", "kling-motion-2-6",
        "Контроль движения и эмоций по вашему референсу",
    ),
    OptionChoice(
        "kling_motion_3_0", "Kling Motion Control 3.0", "kling-motion-3-0",
        "Улучшенный контроль движения, лучшая консистентность лица",
    ),
    OptionChoice(
        "seedance_2", "Seedance 2", "seedance-2",
        "Лучшая видео-модель на рынке (ЭКСКЛЮЗИВ)",
    ),
    OptionChoice(
        "seedance_pro_1_5", "Seedance Pro 1.5", "seedance-pro-1-5",
        "Отличное соотношение цена-качество, идеально для базовых задач",
    ),
    OptionChoice(
        "veo_3_1_fast", "Veo 3.1 Fast", "veo-3-1-fast",
        "Вторая по популярности модель. Идеальная генерация русской речи",
    ),
    OptionChoice(
        "veo_3_1_lite", "Veo 3.1 Lite", "veo-3-1-lite",
        "Лёгкая версия Veo 3.1, пришедшая на замену Veo 3.1 Fast",
    ),
    OptionChoice(
        "wan_2_6", "Wan 2.6", "wan-2-6",
        "Последняя версия видео-модели от Alibaba. Универсальна",
    ),
    OptionChoice(
        "hailuo_2_3_fast", "Hailuo 2.3 Fast", "hailuo-2-3-fast",
        "Быстрая модель от MiniMax",
    ),
    OptionChoice(
        "hailuo_2_3_pro", "Hailuo 2.3 Pro", "hailuo-2-3-pro",
        "Продвинутая версия Hailuo",
    ),
]


# ---- 5. Разрешение видео ---------------------------------------------------

VIDEO_RESOLUTIONS: list[OptionChoice] = [
    OptionChoice("720p", "720p", "720p", "720p — HD"),
    OptionChoice("1080p", "1080p", "1080p", "1080p — Full HD"),
]


# ---- Справочники для поиска по id ------------------------------------------

def _by_id(choices: list[OptionChoice]) -> dict[str, OptionChoice]:
    return {c.id: c for c in choices}


IMAGE_GENERATORS_BY_ID = _by_id(IMAGE_GENERATORS)
ASPECT_RATIOS_BY_ID = _by_id(ASPECT_RATIOS)
IMAGE_RESOLUTIONS_BY_ID = _by_id(IMAGE_RESOLUTIONS)
VIDEO_GENERATORS_BY_ID = _by_id(VIDEO_GENERATORS)
VIDEO_RESOLUTIONS_BY_ID = _by_id(VIDEO_RESOLUTIONS)


# ---- Дефолты (используются если юзер ещё не прошёл мастер) -----------------

DEFAULTS = {
    "image_generator": "nano_banana_2",
    "aspect_ratio": "9:16",
    "image_resolution": "2k",
    "video_generator": "kling_2_6",
    "video_resolution": "1080p",
}


# ---- Функция-рендер полной сводки настроек проекта ------------------------

def render_settings_summary(
    image_generator: str | None,
    aspect_ratio: str | None,
    image_resolution: str | None,
    video_generator: str | None,
    video_resolution: str | None,
) -> str:
    """Человекочитаемая сводка настроек — для карточки проекта в TG."""
    ig = IMAGE_GENERATORS_BY_ID.get(image_generator or "")
    ar = ASPECT_RATIOS_BY_ID.get(aspect_ratio or "")
    ir = IMAGE_RESOLUTIONS_BY_ID.get(image_resolution or "")
    vg = VIDEO_GENERATORS_BY_ID.get(video_generator or "")
    vr = VIDEO_RESOLUTIONS_BY_ID.get(video_resolution or "")
    return (
        f"img-gen: {ig.label if ig else '—'} · "
        f"{ar.label if ar else '—'} · "
        f"{ir.label if ir else '—'}\n"
        f"video-gen: {vg.label if vg else '—'} · "
        f"{vr.label if vr else '—'}"
    )


def render_settings_for_gpt(
    image_generator: str | None,
    aspect_ratio: str | None,
    image_resolution: str | None,
    video_generator: str | None,
    video_resolution: str | None,
) -> str:
    """Блок для вставки в начало master-промта ChatGPT.

    Помогает модели стилизовать image/video промты под возможности
    конкретного генератора.
    """
    ig = IMAGE_GENERATORS_BY_ID.get(image_generator or "")
    ar = ASPECT_RATIOS_BY_ID.get(aspect_ratio or "")
    ir = IMAGE_RESOLUTIONS_BY_ID.get(image_resolution or "")
    vg = VIDEO_GENERATORS_BY_ID.get(video_generator or "")
    vr = VIDEO_RESOLUTIONS_BY_ID.get(video_resolution or "")
    lines = ["=== TECHNICAL SETTINGS (от пользователя) ==="]
    if ig:
        lines.append(
            f"Image generator: {ig.label} — {ig.short_desc}. "
            f"Подгоняй image-промты под её стиль и ограничения."
        )
    if ar:
        lines.append(f"Aspect ratio: {ar.label} — {ar.short_desc}.")
    if ir:
        lines.append(f"Image resolution: {ir.label} — {ir.short_desc}.")
    if vg:
        lines.append(
            f"Video generator: {vg.label} — {vg.short_desc}. "
            f"Подгоняй animation/video-промты под её возможности."
        )
    if vr:
        lines.append(f"Video resolution: {vr.label} — {vr.short_desc}.")
    lines.append("")  # пустая строка-отбивка
    return "\n".join(lines)


# ---- Уникальный ID перед промтом -------------------------------------------

def build_gen_id_prefix(
    project_id: int, frame_number: int | None, short_uuid: str
) -> str:
    """Формат: `[ID: P12-F3-a7f2b01c]`  (или `[ID: P12-HERO-a7f2b01c]`).

    Нужен чтобы однозначно отличать картинки/промты в истории outsee. При
    match'е ищем в DOM текст, содержащий этот префикс, и берём
    соответствующую картинку. Старые картинки (от прошлых попыток) не
    имеют этого конкретного префикса, поэтому не будут случайно выбраны.
    """
    kind = f"F{frame_number}" if frame_number is not None else "HERO"
    return f"[ID: P{project_id}-{kind}-{short_uuid}]"


def prepend_gen_id(prompt: str, gen_id_prefix: str) -> str:
    """Ставит gen_id_prefix на первую строку промта (перед оригинальным текстом)."""
    prompt = (prompt or "").lstrip()
    return f"{gen_id_prefix}\n\n{prompt}"
