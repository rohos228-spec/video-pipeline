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

Шаги (после редизайна):
  1: plan      → planning            → plan_ready
  2: script    → scripting           → script_ready
  3: split     → splitting           → frames_ready
  4: objects   → (sub-menu: persons/items)        → hero_ready или items_ready
       4a:  persons → generating_hero  → hero_ready
       4b:  items   → generating_items → items_ready
  5: enrich_1 → enriching_1          → enrich_1_ready  (xlsx round-trip)
  6: enrich_2 → enriching_2          → enrich_2_ready  (доступен по «+ слот»)
  7: enrich_3 → enriching_3          → enrich_3_ready
  …  (до 5 слотов; «+ Добавить слот» наращивает projects.enrich_slots_count)
  8: img_pr   → generating_image_prompts → image_prompts_ready
  9: img      → generating_images   → images_ready
 10: anim_pr  → generating_animation_prompts → animation_prompts_ready
 11: video    → generating_videos   → videos_ready
 12: audio    → generating_audio    → audio_ready
 13: assemble → assembling          → assembled
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
#
# ВАЖНО про шаг 4 («Объекты»):
#   - hero_ready (персонажи готовы) и items_ready (предметы готовы) —
#     ОБА являются «выходом» шага 4. items_ready идёт выше по порядку,
#     чтобы покрывать кейс «юзер сделал и персонажей, и предметы».
#   - Если предметов в проекте нет, юзер пропустит «Предметы» и сразу
#     перейдёт на enrich_1 от hero_ready — этого достаточно (см.
#     requires шага 5).
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
    ProjectStatus.generating_items: 9,
    ProjectStatus.items_ready: 10,
    ProjectStatus.enriching_1: 11,
    ProjectStatus.enrich_1_ready: 12,
    ProjectStatus.enriching_2: 13,
    ProjectStatus.enrich_2_ready: 14,
    ProjectStatus.enriching_3: 15,
    ProjectStatus.enrich_3_ready: 16,
    ProjectStatus.enriching_4: 17,
    ProjectStatus.enrich_4_ready: 18,
    ProjectStatus.enriching_5: 19,
    ProjectStatus.enrich_5_ready: 20,
    ProjectStatus.generating_image_prompts: 21,
    ProjectStatus.image_prompts_ready: 22,
    ProjectStatus.generating_images: 23,
    ProjectStatus.images_ready: 24,
    ProjectStatus.generating_animation_prompts: 25,
    ProjectStatus.animation_prompts_ready: 26,
    ProjectStatus.generating_videos: 27,
    ProjectStatus.videos_ready: 28,
    ProjectStatus.generating_audio: 29,
    ProjectStatus.audio_ready: 30,
    ProjectStatus.assembling: 31,
    ProjectStatus.assembled: 32,
    ProjectStatus.publishing: 33,
    ProjectStatus.published: 34,
    ProjectStatus.paused: 0,
    ProjectStatus.failed: 0,
}


# Список (running_status, ready_status) для каждого enrich-слота 1..5.
# Используется при динамической сборке STEPS_FOR(project) (см. ниже),
# чтобы количество кнопок «Доп работа с EXCEL #i» соответствовало
# projects.enrich_slots_count (по умолчанию 3, до 5).
ENRICH_RUNNING: list[ProjectStatus] = [
    ProjectStatus.enriching_1,
    ProjectStatus.enriching_2,
    ProjectStatus.enriching_3,
    ProjectStatus.enriching_4,
    ProjectStatus.enriching_5,
]
ENRICH_READY: list[ProjectStatus] = [
    ProjectStatus.enrich_1_ready,
    ProjectStatus.enrich_2_ready,
    ProjectStatus.enrich_3_ready,
    ProjectStatus.enrich_4_ready,
    ProjectStatus.enrich_5_ready,
]
MAX_ENRICH_SLOTS = 5


def _objects_requires_for_step5() -> ProjectStatus:
    """Что должно быть достигнуто, чтобы шаг 5 (enrich_1) был доступен?
    Поскольку «Предметы» опциональны, считаем минимально достаточным
    hero_ready. Если юзер сделал и предметы — items_ready по порядку
    выше, значит тоже >= hero_ready, prerequisite пройден."""
    return ProjectStatus.hero_ready


def steps_for(project: Project | None) -> list[StepDef]:
    """Динамический список шагов меню для проекта.

    Шаги 1-3 без изменений. Шаг 4 «Объекты» — wrapper над двумя
    суб-генерациями (running=generating_hero — для совместимости с
    логикой step_icon/runnable; sub-menu рисуется отдельно). Слотов
    «Доп работа с EXCEL» — столько, сколько указано в
    project.enrich_slots_count (от 1 до 5, дефолт 3). Затем идут
    шаги «Промты картинок» → «Финальная сборка», с requires=
    enrich_<N>_ready, где N — последний активный слот.

    Если project=None (например в /menu списках) — берётся 3 слота
    по умолчанию.
    """
    n_slots = (
        max(1, min(MAX_ENRICH_SLOTS, project.enrich_slots_count or 3))
        if project is not None
        else 3
    )
    steps: list[StepDef] = [
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
            4, "objects", "Объекты",
            ProjectStatus.generating_hero,  # для подсветки ⏳ при персонажах
            ProjectStatus.hero_ready,
            ProjectStatus.frames_ready,
        ),
    ]
    # Слоты enrich. Первый зависит от шага 4, дальше — от предыдущего.
    prev_ready: ProjectStatus = _objects_requires_for_step5()
    n = 5
    for i in range(1, n_slots + 1):
        steps.append(
            StepDef(
                n, f"enrich_{i}", f"Доп работа с EXCEL #{i}",
                ENRICH_RUNNING[i - 1], ENRICH_READY[i - 1],
                prev_ready,
            )
        )
        prev_ready = ENRICH_READY[i - 1]
        n += 1
    # Финальные шаги. Первый из них требует последний enrich_<n_slots>_ready.
    steps.extend([
        StepDef(
            n, "img_pr", "Промты картинок",
            ProjectStatus.generating_image_prompts, ProjectStatus.image_prompts_ready,
            prev_ready,
        ),
        StepDef(
            n + 1, "img", "Картинки",
            ProjectStatus.generating_images, ProjectStatus.images_ready,
            ProjectStatus.image_prompts_ready,
        ),
        StepDef(
            n + 2, "anim_pr", "Промты анимации",
            ProjectStatus.generating_animation_prompts, ProjectStatus.animation_prompts_ready,
            ProjectStatus.images_ready,
        ),
        StepDef(
            n + 3, "video", "Видео",
            ProjectStatus.generating_videos, ProjectStatus.videos_ready,
            ProjectStatus.animation_prompts_ready,
        ),
        StepDef(
            n + 4, "audio", "Аудио",
            ProjectStatus.generating_audio, ProjectStatus.audio_ready,
            ProjectStatus.videos_ready,
        ),
        StepDef(
            n + 5, "assemble", "Финальная сборка",
            ProjectStatus.assembling, ProjectStatus.assembled,
            ProjectStatus.audio_ready,
        ),
    ])
    return steps


# Базовый список (3 слота enrich) — для обратной совместимости
# импортов `from app.telegram.menu import STEPS`. Большинство мест
# в коде ходят через `step_by_code()`, который работает поверх этого
# базового списка.
STEPS: list[StepDef] = steps_for(None)


# Полный лукап-словарь: включает все 5 возможных enrich-слотов,
# даже если в дефолтном STEPS их только 3. Чтобы step_by_code(...)
# не возвращал None при клике на «5. Доп работа с EXCEL #5», когда
# enrich_slots_count расширен до 5.
_STEP_BY_CODE: dict[str, StepDef] = {s.code: s for s in steps_for(None)}
for _slot in range(1, MAX_ENRICH_SLOTS + 1):
    _code = f"enrich_{_slot}"
    if _code not in _STEP_BY_CODE:
        _STEP_BY_CODE[_code] = StepDef(
            -1, _code, f"Доп работа с EXCEL #{_slot}",
            ENRICH_RUNNING[_slot - 1], ENRICH_READY[_slot - 1],
            ENRICH_READY[_slot - 2] if _slot > 1 else _objects_requires_for_step5(),
        )


def step_by_code(code: str) -> StepDef | None:
    return _STEP_BY_CODE.get(code)


def status_order(s: ProjectStatus) -> int:
    return _STATUS_ORDER.get(s, 0)


def step_icon(step: StepDef, project_status: ProjectStatus) -> str:
    """Возвращает значок-индикатор статуса шага.
      ✅ — пройден; ⏳ — выполняется; ⬜ — не пройден.

    `failed` больше не используется (воркер вместо него откатывает статус
    к prerequisite упавшего шага), поэтому никакого спец-кейса для failed
    тут нет.
    """
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

    steps = steps_for(project)
    for s in steps:
        icon = step_icon(s, project.status)
        runnable = is_step_runnable(s, project.status) and wiz_ok

        # Спец-кейс шаг 4 «Объекты»: считаем «running», если проект
        # в любом из generating_hero / generating_items.
        if s.code == "objects":
            is_running_now = project.status in (
                ProjectStatus.generating_hero,
                ProjectStatus.generating_items,
            )
        else:
            is_running_now = project.status is s.running_status

        # Спец-иконка для «Объекты»: ✅ если или hero_ready, или items_ready.
        if s.code == "objects" and not is_running_now:
            done = status_order(project.status) >= status_order(ProjectStatus.hero_ready)
            icon = "✅" if done else "⬜"

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

    # Кнопка «➕ Добавить слот» — рисуется только если ещё не достигли
    # лимита и мастер пройден. Инкрементит project.enrich_slots_count.
    if wiz_ok and (project.enrich_slots_count or 3) < MAX_ENRICH_SLOTS:
        rows.append([
            InlineKeyboardButton(
                text="➕ Добавить слот «Доп работа с EXCEL»",
                callback_data=f"proj:{project.id}:enrich_add_slot",
            )
        ])

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


def objects_submenu_kb(project: Project) -> InlineKeyboardMarkup:
    """Подменю шага 4 «Объекты» — выбор между «Персонажи» и «Предметы».

    Иконки:
      ✅ — этот суб-шаг уже сделан (hero_ready / items_ready)
      ⏳ — этот суб-шаг сейчас идёт (generating_hero / generating_items)
      ▶ — можно запустить
    """
    rows: list[list[InlineKeyboardButton]] = []

    # «Персонажи» (старый hero)
    if project.status is ProjectStatus.generating_hero:
        chars_label = "⏳ Персонажи · идёт…"
    elif status_order(project.status) >= status_order(ProjectStatus.hero_ready):
        chars_label = "✅ Персонажи (перегенерировать)"
    else:
        chars_label = "▶ Персонажи"
    rows.append([
        InlineKeyboardButton(
            text=chars_label,
            callback_data=f"proj:{project.id}:objects:persons",
        )
    ])

    # «Предметы» — доступны только если hero_ready (требование).
    can_items = status_order(project.status) >= status_order(ProjectStatus.hero_ready)
    if project.status is ProjectStatus.generating_items:
        items_label = "⏳ Предметы · идёт…"
    elif status_order(project.status) >= status_order(ProjectStatus.items_ready):
        items_label = "✅ Предметы (перегенерировать)"
    elif can_items:
        items_label = "▶ Предметы"
    else:
        items_label = "🔒 Предметы (сначала персонажи)"
    rows.append([
        InlineKeyboardButton(
            text=items_label,
            callback_data=(
                f"proj:{project.id}:objects:items" if can_items else "noop"
            ),
        )
    ])

    rows.append([
        InlineKeyboardButton(
            text="⬅ Назад в меню проекта",
            callback_data=f"proj:{project.id}:menu",
        )
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
