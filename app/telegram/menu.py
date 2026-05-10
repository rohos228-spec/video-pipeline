"""Меню Telegram-бота для ручного управления пайплайном.

Иерархия экранов:
  /menu                              → главное меню (новый/существующие)
  cb=menu:new                        → попросить тему → cb=menu:topic_set
  cb=menu:list                       → список проектов
  cb=proj:<id>:menu                  → меню проекта (10 шагов + xlsx + удалить)
  cb=proj:<id>:step:<n>              → запустить шаг N
  cb=proj:<id>:dl_xlsx               → скачать xlsx
  cb=proj:<id>:reload_xlsx           → перечитать xlsx → БД
  cb=proj:<id>:delete                → удалить проект (после подтверждения)
  cb=proj:<id>:delete_yes            → подтверждённое удаление

Шаги:
  1: plan      → planning            → plan_ready
  2: script    → scripting           → script_ready
  3: split     → splitting           → frames_ready
  4: hero      → generating_hero     → hero_ready
  5: img_pr    → generating_image_prompts → image_prompts_ready
  6: img       → generating_images   → images_ready
  7: anim_pr   → generating_animation_prompts → animation_prompts_ready
  8: video     → generating_videos   → videos_ready
  9: audio     → generating_audio    → audio_ready
 10: assemble  → assembling          → assembled
"""

from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.models import Project, ProjectStatus

# Тексты кнопок постоянной reply-клавиатуры (видна всегда внизу TG над полем
# ввода). Эти строки используются в bot.py для распознавания нажатий.
PERSISTENT_HOME_TEXT = "🏠 Главное меню"
PERSISTENT_LAST_TEXT = "📁 Последний проект"
PERSISTENT_BACK_TEXT = "⬅ Назад"


def persistent_reply_kb() -> ReplyKeyboardMarkup:
    """Постоянная reply-клавиатура внизу чата TG.

    Telegram сохраняет её до тех пор, пока ей не пришлют новую (или
    `ReplyKeyboardRemove`). Поэтому достаточно один раз послать её на
    `/start` или `/menu` — она остаётся видна на всех последующих
    экранах с inline-кнопками.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=PERSISTENT_HOME_TEXT),
                KeyboardButton(text=PERSISTENT_LAST_TEXT),
            ],
            [KeyboardButton(text=PERSISTENT_BACK_TEXT)],
        ],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Напиши команду или жми кнопки",
    )


@dataclass(frozen=True)
class StepDef:
    n: int
    code: str  # короткий код для callback
    title: str
    running_status: ProjectStatus
    ready_status: ProjectStatus
    requires: ProjectStatus | None  # должен быть достигнут (или превзойдён) этот «ready»


# Порядок «достижимости» статусов — для проверки prerequisite.
# Чем выше число, тем «дальше» по пайплайну проект.
_STATUS_ORDER: dict[ProjectStatus, int] = {
    ProjectStatus.new: 0,
    ProjectStatus.planning: 1,
    ProjectStatus.plan_ready: 2,
    ProjectStatus.scripting: 3,
    ProjectStatus.script_ready: 4,
    ProjectStatus.splitting: 5,
    ProjectStatus.frames_ready: 6,
    ProjectStatus.generating_hero: 7,
    ProjectStatus.hero_ready: 8,
    ProjectStatus.generating_image_prompts: 9,
    ProjectStatus.image_prompts_ready: 10,
    ProjectStatus.generating_images: 11,
    ProjectStatus.images_ready: 12,
    ProjectStatus.generating_animation_prompts: 13,
    ProjectStatus.animation_prompts_ready: 14,
    ProjectStatus.generating_videos: 15,
    ProjectStatus.videos_ready: 16,
    ProjectStatus.generating_audio: 17,
    ProjectStatus.audio_ready: 18,
    ProjectStatus.assembling: 19,
    ProjectStatus.assembled: 20,
    ProjectStatus.publishing: 21,
    ProjectStatus.published: 22,
    ProjectStatus.paused: 0,
    ProjectStatus.failed: 0,
}


STEPS: list[StepDef] = [
    StepDef(
        1, "plan", "План",
        ProjectStatus.planning, ProjectStatus.plan_ready, None,
    ),
    StepDef(
        2, "script", "Закадровый текст",
        ProjectStatus.scripting, ProjectStatus.script_ready,
        ProjectStatus.plan_ready,
    ),
    StepDef(
        3, "split", "Разбивка на блоки",
        ProjectStatus.splitting, ProjectStatus.frames_ready,
        ProjectStatus.script_ready,
    ),
    StepDef(
        4, "hero", "Hero-картинка",
        ProjectStatus.generating_hero, ProjectStatus.hero_ready,
        ProjectStatus.frames_ready,
    ),
    StepDef(
        5, "img_pr", "Промты картинок",
        ProjectStatus.generating_image_prompts, ProjectStatus.image_prompts_ready,
        ProjectStatus.hero_ready,
    ),
    StepDef(
        6, "img", "Картинки",
        ProjectStatus.generating_images, ProjectStatus.images_ready,
        ProjectStatus.image_prompts_ready,
    ),
    StepDef(
        7, "anim_pr", "Промты анимации",
        ProjectStatus.generating_animation_prompts, ProjectStatus.animation_prompts_ready,
        ProjectStatus.images_ready,
    ),
    StepDef(
        8, "video", "Видео",
        ProjectStatus.generating_videos, ProjectStatus.videos_ready,
        ProjectStatus.animation_prompts_ready,
    ),
    StepDef(
        9, "audio", "Аудио",
        ProjectStatus.generating_audio, ProjectStatus.audio_ready,
        ProjectStatus.videos_ready,
    ),
    StepDef(
        10, "assemble", "Финальная сборка",
        ProjectStatus.assembling, ProjectStatus.assembled,
        ProjectStatus.audio_ready,
    ),
]


_STEP_BY_CODE: dict[str, StepDef] = {s.code: s for s in STEPS}


def step_by_code(code: str) -> StepDef | None:
    return _STEP_BY_CODE.get(code)


def status_order(s: ProjectStatus) -> int:
    return _STATUS_ORDER.get(s, 0)


def step_icon(step: StepDef, project_status: ProjectStatus) -> str:
    """Возвращает значок-индикатор статуса шага.
      ✅ — пройден; ⏳ — выполняется; ⬜ — не пройден; ❌ — упал.
    """
    if project_status is ProjectStatus.failed:
        # На каком этапе упало — узнать сложно; рисуем «❌» только на текущем шаге
        # пайплайна (определить «текущий» нельзя без доп. поля). Пусть всё «⬜».
        return "⬜"
    if project_status is step.running_status:
        return "⏳"
    if status_order(project_status) >= status_order(step.ready_status):
        return "✅"
    return "⬜"


def is_step_runnable(step: StepDef, project_status: ProjectStatus) -> bool:
    """Можно ли запустить шаг прямо сейчас?
       Запуск разрешён, если предыдущий шаг достиг своего «ready»-состояния
       (или это первый шаг). Перезапуск разрешён всегда (если уже пройден)."""
    if step.requires is None:
        return True
    return status_order(project_status) >= status_order(step.requires)


def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📁 Новый проект", callback_data="menu:new")],
            [InlineKeyboardButton(text="📋 Существующие проекты", callback_data="menu:list")],
        ]
    )


def _wizard_complete(project: Project) -> bool:
    """Заполнены ли все 5 настроек? До этого шаги 1-10 недоступны."""
    return all(
        getattr(project, f, None) not in (None, "")
        for f in (
            "image_generator",
            "aspect_ratio",
            "image_resolution",
            "video_generator",
            "video_resolution",
        )
    )


def project_menu_kb(project: Project) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    wiz_ok = _wizard_complete(project)

    # Пока мастер не пройден — первая строка меню это большая кнопка
    # «Заполнить настройки».
    if not wiz_ok:
        rows.append([
            InlineKeyboardButton(
                text="⚙ Заполнить настройки (5 вопросов)",
                callback_data=f"wiz:{project.id}:start",
            )
        ])

    for s in STEPS:
        icon = step_icon(s, project.status)
        runnable = is_step_runnable(s, project.status) and wiz_ok
        # Если шаг сейчас в running-статусе — НЕ блокируем кнопку. Раньше
        # для всех шагов кроме hero ставили `cb="noop"` («Эта кнопка пока
        # недоступна»), и юзер ничего не мог сделать когда воркер падал
        # на playwright-ошибке и оставлял проект в зомби-статусе типа
        # `generating_image_prompts` — кнопка шага становилась мёртвой,
        # из меню некуда выйти кроме как править SQLite вручную.
        # Теперь все шаги обрабатываются `on_project_step` (там есть
        # отдельная логика «zombie status → перезапуск»).
        is_running_now = project.status is s.running_status
        if is_running_now:
            label = f"{icon} {s.n}. {s.title} · идёт… (тык — управление)"
            cb = f"proj:{project.id}:step:{s.code}"
        elif runnable:
            label = f"{icon} {s.n}. {s.title}"
            cb = f"proj:{project.id}:step:{s.code}"
        else:
            lock = "🔒" if wiz_ok else "⚙"
            label = f"{icon} {s.n}. {s.title}  {lock}"
            cb = "noop" if wiz_ok else f"wiz:{project.id}:start"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])

    # Шестая строка: настройки (пересмотреть / сбросить)
    if wiz_ok:
        rows.append([
            InlineKeyboardButton(
                text="⚙ Настройки", callback_data=f"wiz:{project.id}:start"
            ),
            InlineKeyboardButton(
                text="↻ Сбросить настройки",
                callback_data=f"wiz:{project.id}:reset",
            ),
        ])

    # Библиотека мастер-промтов: посмотреть/сменить выбор по любому шагу.
    rows.append([
        InlineKeyboardButton(
            text="🧰 Промты", callback_data=f"pov:{project.id}"
        ),
    ])

    rows.append([
        InlineKeyboardButton(text="📥 Скачать xlsx", callback_data=f"proj:{project.id}:dl_xlsx"),
        InlineKeyboardButton(text="🔄 Перечитать xlsx", callback_data=f"proj:{project.id}:reload_xlsx"),
    ])
    rows.append([
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"proj:{project.id}:delete"),
        InlineKeyboardButton(text="⬅ Меню", callback_data="menu:root"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def script_step_kb(
    pid: int, *, voiceover_exists: bool
) -> InlineKeyboardMarkup:
    """Подменю шага 2 «Закадровый текст».

    Показывается после клика на кнопку шага 2 в меню проекта.
    Если уже есть сгенерированный voiceover.txt — даём кнопку посмотреть
    текущий файл; в любом случае предлагаем «сгенерировать заново».
    """
    rows: list[list[InlineKeyboardButton]] = []
    if voiceover_exists:
        rows.append([
            InlineKeyboardButton(
                text="📄 Посмотреть voiceover.txt",
                callback_data=f"proj:{pid}:script_view",
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="✏️ Заменить voiceover.txt",
                callback_data=f"proj:{pid}:script_replace",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="▶ Сгенерировать заново" if voiceover_exists else "▶ Сгенерировать",
            callback_data=f"proj:{pid}:script_regen",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅ Назад в меню проекта",
            callback_data=f"proj:{pid}:menu",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def project_header(project: Project) -> str:
    from app.generation_options import render_settings_summary

    settings_line = render_settings_summary(
        getattr(project, "image_generator", None),
        getattr(project, "aspect_ratio", None),
        getattr(project, "image_resolution", None),
        getattr(project, "video_generator", None),
        getattr(project, "video_resolution", None),
        image_relax=getattr(project, "image_relax", None),
        video_relax=getattr(project, "video_relax", None),
    )
    return (
        f"📁 Проект #{project.id} «{project.topic}»\n"
        f"slug: <code>{project.slug}</code>\n"
        f"hero: {project.hero_mode}\n"
        f"статус: <b>{project.status.value}</b>\n"
        f"{settings_line}"
    )
