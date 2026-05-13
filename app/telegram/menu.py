"""Меню Telegram-бота для ручного управления пайплайном.

Иерархия экранов:
  /menu                              → главное меню (новый/существующие)
  cb=menu:new                        → попросить тему → cb=menu:topic_set
  cb=menu:list                       → список проектов
  cb=proj:<id>:menu                  → меню проекта (шаги + xlsx + удалить)
  cb=proj:<id>:step:<code>           → запустить шаг
  cb=proj:<id>:dl_xlsx               → скачать xlsx
  cb=proj:<id>:reload_xlsx           → перечитать xlsx → БД
  cb=proj:<id>:delete                → удалить проект (после подтверждения)
  cb=proj:<id>:delete_yes            → подтверждённое удаление

Шаги (после редизайна с группировкой):
  1: plan      → planning            → plan_ready
  2: script    → scripting           → script_ready
  3: split     → splitting           → frames_ready
  4: objects   → (sub-menu: persons/items)        → hero_ready или items_ready
       4a:  persons → generating_hero  → hero_ready
       4b:  items   → generating_items → items_ready
  5: enrich   → (sub-menu: enrich_1..N + «➕ слот»)
       5.1:  enrich_1 → enriching_1 → enrich_1_ready (xlsx round-trip)
       5.2:  enrich_2 → enriching_2 → enrich_2_ready
       5.3:  enrich_3 → enriching_3 → enrich_3_ready (по дефолту 3 слота)
       …  (до 5 слотов; «+ Добавить слот» наращивает enrich_slots_count)
  6: img_pr   → generating_image_prompts → image_prompts_ready
  7: img      → generating_images   → images_ready
  8: anim_pr  → generating_animation_prompts → animation_prompts_ready
  9: video    → generating_videos   → videos_ready
 10: audio    → generating_audio    → audio_ready
 11: assemble → assembling          → assembled
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
from app.services.project_state import is_running_status

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


def enabled_enrich_slots(project: Project | None) -> int:
    """Сколько enrich-слотов реально включено у проекта (1..5).
    Если project=None или поле не выставлено — дефолт 3."""
    if project is None:
        return 3
    n = project.enrich_slots_count or 3
    return max(1, min(MAX_ENRICH_SLOTS, n))


def steps_for(project: Project | None) -> list[StepDef]:
    """Динамический список шагов меню для проекта.

    После последнего редизайна все enrich-слоты схлопнуты в ОДИН пункт
    меню «5. Доп работа с EXCEL» (wrapper). При клике открывается
    суб-меню с N кнопками «Доп работа с EXCEL #i» и кнопкой
    «➕ Добавить слот». Сами слоты ПО-ПРЕЖНЕМУ имеют отдельные коды
    `enrich_1..enrich_5` в `_STEP_BY_CODE` — это нужно, чтобы
    `step_by_code("enrich_1")` находил StepDef для запуска нужного
    `enriching_<i>` running-статуса в воркере, и чтобы prompt-picker
    видел их в `STEP_FOLDERS`.

    Если project=None (например в /menu списках) — берётся 3 слота
    по умолчанию.
    """
    n_slots = enabled_enrich_slots(project)
    # Wrapper-шаг 5 «Доп работа с EXCEL»:
    #   - running_status:  enriching_1 (плейсхолдер, иконка «⏳»
    #     спец-кейсом обрабатывается в step_icon: ⏳ если статус ∈
    #     enriching_1..5)
    #   - ready_status:    enrich_<n_slots>_ready (✅ только когда
    #     ПОСЛЕДНИЙ активный слот выполнен)
    #   - requires:        hero_ready (т.к. предметы опциональны)
    enrich_ready = ENRICH_READY[n_slots - 1]
    return [
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
        StepDef(
            5, "enrich", "Доп работа с EXCEL",
            ProjectStatus.enriching_1,  # плейсхолдер; ⏳ спец-кейсом
            enrich_ready,
            _objects_requires_for_step5(),
        ),
        StepDef(
            6, "img_pr", "Промты картинок",
            ProjectStatus.generating_image_prompts,
            ProjectStatus.image_prompts_ready,
            enrich_ready,
        ),
        StepDef(
            7, "img", "Картинки",
            ProjectStatus.generating_images, ProjectStatus.images_ready,
            ProjectStatus.image_prompts_ready,
        ),
        StepDef(
            8, "anim_pr", "Промты анимации",
            ProjectStatus.generating_animation_prompts,
            ProjectStatus.animation_prompts_ready,
            ProjectStatus.images_ready,
        ),
        StepDef(
            9, "video", "Видео",
            ProjectStatus.generating_videos, ProjectStatus.videos_ready,
            ProjectStatus.animation_prompts_ready,
        ),
        StepDef(
            10, "audio", "Аудио",
            ProjectStatus.generating_audio, ProjectStatus.audio_ready,
            ProjectStatus.videos_ready,
        ),
        StepDef(
            11, "assemble", "Финальная сборка",
            ProjectStatus.assembling, ProjectStatus.assembled,
            ProjectStatus.audio_ready,
        ),
    ]


# Список StepDef'ов для отдельных enrich-слотов (запускаются из
# enrich_submenu_kb, имеют running/ready-статусы под каждый слот).
# Используется step_by_code("enrich_<i>") и as-is воркером.
def _enrich_slot_step(slot: int) -> StepDef:
    """Создать StepDef для отдельного enrich-слота 1..5."""
    return StepDef(
        # Номер шага «-1» означает «не присутствует в основном списке»
        # (под кнопкой 5 «Доп работа с EXCEL» — sub-step).
        -1, f"enrich_{slot}", f"Доп работа с EXCEL #{slot}",
        ENRICH_RUNNING[slot - 1], ENRICH_READY[slot - 1],
        # Префиквизит:
        #   - слот #1 — после «Объектов» (hero_ready)
        #   - слот #N>1 — после предыдущего enrich_(N-1)_ready
        ENRICH_READY[slot - 2] if slot > 1 else _objects_requires_for_step5(),
    )


# Базовый список (wrapper «Доп работа с EXCEL») — для обратной
# совместимости импортов `from app.telegram.menu import STEPS`.
# Большинство мест в коде ходят через `step_by_code()`, который
# работает поверх этого базового списка + sub-step'ы enrich_1..5.
STEPS: list[StepDef] = steps_for(None)


# Полный лукап-словарь: включает все wrapper-шаги основного меню
# плюс sub-step'ы:
#   - "hero"  — старая Hero-логика (sub-step под «Объекты»)
#   - "items" — реф-картинки предметов (sub-step под «Объекты»)
#   - "enrich_1..5" — sub-step'ы под «Доп работа с EXCEL»
# Они недоступны напрямую из основного меню (n=-1), но нужны для:
#   - step_by_code("hero") при эмуляции через on_objects_persons
#   - step_by_code("enrich_1") при клике в enrich submenu
#   - prompt-picker по коду шага
_STEP_BY_CODE: dict[str, StepDef] = {s.code: s for s in STEPS}
# Sub-step «Персонажи» (старая Hero-логика). running=generating_hero,
# ready=hero_ready, requires=frames_ready (см. on_project_step:
# if step.code == "hero").
_STEP_BY_CODE["hero"] = StepDef(
    -1, "hero", "Персонажи",
    ProjectStatus.generating_hero, ProjectStatus.hero_ready,
    ProjectStatus.frames_ready,
)
# Sub-step «Предметы». running=generating_items, ready=items_ready,
# requires=hero_ready.
_STEP_BY_CODE["items"] = StepDef(
    -1, "items", "Предметы",
    ProjectStatus.generating_items, ProjectStatus.items_ready,
    ProjectStatus.hero_ready,
)
# Sub-step'ы enrich_1..5.
for _slot in range(1, MAX_ENRICH_SLOTS + 1):
    _code = f"enrich_{_slot}"
    _STEP_BY_CODE[_code] = _enrich_slot_step(_slot)


def step_by_code(code: str) -> StepDef | None:
    return _STEP_BY_CODE.get(code)


def step_by_running_status(running_status: ProjectStatus) -> StepDef | None:
    """Найти StepDef по running_status. Учитывает как «верхние» шаги
    основного меню (см. steps_for(None)), так и sub-step'ы для
    hero/items/enrich_1..5 — последнее важно для rollback'а при
    failed-шаге.

    Sub-step'ы матчатся первыми, т.к. они точнее (например wrapper
    "objects" имеет running=generating_hero, но при failed-rollback
    хотим вернуться к requires sub-step'а "hero", т.е. frames_ready —
    это совпадает с requires "objects", так что разницы нет, но
    приоритет на sub-step'ах — на будущее).
    """
    # Sub-step'ы (точнее, чем wrapper'ы).
    for code in ("hero", "items", *(f"enrich_{i}" for i in range(1, MAX_ENRICH_SLOTS + 1))):
        sd = _STEP_BY_CODE.get(code)
        if sd is not None and sd.running_status is running_status:
            return sd
    # Затем — среди базовых шагов основного меню (1..11).
    for sd in STEPS:
        # Wrapper'ы (objects/enrich) уже покрыты sub-step'ами выше.
        if sd.code in ("objects", "enrich"):
            continue
        if sd.running_status is running_status:
            return sd
    return None


def status_order(s: ProjectStatus) -> int:
    return _STATUS_ORDER.get(s, 0)


def step_icon(step: StepDef, project_status: ProjectStatus) -> str:
    """Возвращает значок-индикатор статуса шага.
      ✅ — пройден; ⏳ — выполняется; ⬜ — не пройден.

    `failed` больше не используется (воркер вместо него откатывает статус
    к prerequisite упавшего шага), поэтому никакого спец-кейса для failed
    тут нет.

    Спец-кейсы:
      - «objects»: ⏳ если статус ∈ {generating_hero, generating_items};
        ✅ — если достигнут hero_ready.
      - «enrich» (wrapper-шаг 5): ⏳ если статус ∈ enriching_1..5;
        ✅ — если достигнут enrich_<n_slots>_ready (последний активный).
    """
    if step.code == "enrich":
        if project_status in ENRICH_RUNNING:
            return "⏳"
        if status_order(project_status) >= status_order(step.ready_status):
            return "✅"
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

    steps = steps_for(project)
    for s in steps:
        icon = step_icon(s, project.status)

        # Спец-кейс шаг 4 «Объекты»: считаем «running», если проект
        # в любом из generating_hero / generating_items.
        if s.code == "objects":
            is_running_now = project.status in (
                ProjectStatus.generating_hero,
                ProjectStatus.generating_items,
            )
        # Спец-кейс шаг 5 «Доп работа с EXCEL» (wrapper):
        # «running», если статус в любом из enriching_1..5.
        elif s.code == "enrich":
            is_running_now = project.status in ENRICH_RUNNING
        else:
            is_running_now = project.status is s.running_status

        # Спец-иконка для «Объекты»: ✅ если или hero_ready, или items_ready.
        if s.code == "objects" and not is_running_now:
            done = status_order(project.status) >= status_order(ProjectStatus.hero_ready)
            icon = "✅" if done else "⬜"

        if is_running_now:
            label = f"{icon} {s.n}. {s.title} · идёт… (тык — управление)"
            cb = f"proj:{project.id}:step:{s.code}"
        else:
            # По требованию: убираем замки 🔒/⚙ со всех пунктов главного
            # меню — все шаги всегда кликабельны. Внутренняя валидация
            # (мастер заполнен / предыдущий шаг выполнен) остаётся внутри
            # step-handler'а, где это нужно.
            label = f"{icon} {s.n}. {s.title}"
            cb = f"proj:{project.id}:step:{s.code}"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])

    # NB: кнопки «➕ Добавить слот» в основном меню больше нет —
    # она переехала внутрь подменю «Доп работа с EXCEL» (enrich_submenu_kb).

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

    # ⏹ Остановить — всегда видим. Останавливает running-статус воркера
    # и/или отменяет xlsx-flow (plan/script/split).
    rows.append([
        InlineKeyboardButton(
            text="⏹ Остановить текущий шаг",
            callback_data=f"proj:{project.id}:stop_running",
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
    # «🧾 Из EXCEL» — новый flow: персонажи берутся из листа «Персонажи»
    # project.xlsx (R1=id, R3=имя, R4=внешность, R5=одежда, R6=характер,
    # R7=правила). Для каждого пользователь выбирает промт, дальше
    # генерация идёт автоматически. Реф-вариации (по ID в R7) — без GPT.
    if project.status is not ProjectStatus.generating_hero:
        rows.append([
            InlineKeyboardButton(
                text="🧾 Из EXCEL",
                callback_data=f"proj:{project.id}:objects:persons_xlsx",
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


def enrich_submenu_kb(project: Project) -> InlineKeyboardMarkup:
    """Подменю шага 5 «Доп работа с EXCEL».

    Содержит:
      - N кнопок «Доп работа с EXCEL #1..#N» (N = enrich_slots_count).
        ВСЕ слоты доступны для клика — даже «заблокированные» (юзер
        может заранее сконфигурить шаблон/сопр. сообщение, а запуск
        будет проверен только в picker'е по нажатию «▶ Запустить шаг»).
      - Кнопку «▶▶ Запустить все слоты подряд» (когда слот #1 готов
        к запуску). Выставляет meta['enrich_auto_chain_to'] = N и
        ставит статус enriching_1 — воркер сам пройдёт всю цепочку.
      - Кнопку «➕ Добавить слот» (если N < 5).

    Иконки слотов:
      ✅ — этот слот уже выполнен (enrich_<i>_ready достигнут)
      ⏳ — этот слот сейчас выполняется (status == enriching_<i>)
      ▶ — можно запустить (предыдущий слот готов или это слот #1 и
          hero_ready достигнут)
      ⚙ — заблокирован для запуска (предыдущий слот не готов), но
          можно зайти в picker и сконфигурить заранее
    """
    rows: list[list[InlineKeyboardButton]] = []
    n_slots = enabled_enrich_slots(project)
    slot1_can_run = (
        status_order(project.status)
        >= status_order(_objects_requires_for_step5())
    )
    any_running = project.status in ENRICH_RUNNING

    for i in range(1, n_slots + 1):
        running = ENRICH_RUNNING[i - 1]
        ready = ENRICH_READY[i - 1]
        if i == 1:
            prereq = _objects_requires_for_step5()
        else:
            prereq = ENRICH_READY[i - 2]

        is_running = project.status is running
        is_done = status_order(project.status) >= status_order(ready)
        can_run = status_order(project.status) >= status_order(prereq)

        if is_running:
            label = f"⏳ Доп работа с EXCEL #{i} · идёт…"
        elif is_done:
            label = f"✅ Доп работа с EXCEL #{i} (перезапустить)"
        elif can_run:
            label = f"▶ Доп работа с EXCEL #{i}"
        else:
            # Раньше тут стоял noop-замок. Теперь даём войти в
            # picker — настроить шаблон + сопр. сообщение заранее.
            label = f"⚙ Доп работа с EXCEL #{i} (настроить заранее)"
        # ВСЕ слоты ведут на step-handler — он покажет picker.
        cb = f"proj:{project.id}:step:enrich_{i}"
        rows.append([InlineKeyboardButton(text=label, callback_data=cb)])

    # «▶▶ Запустить все слоты подряд» — стартует enriching_1, после
    # каждого ready=> воркер автоматически переводит в следующий
    # enriching_<i+1>. Показываем только когда слот #1 готов к
    # запуску и сейчас ничего не выполняется.
    if slot1_can_run and not any_running:
        rows.append([
            InlineKeyboardButton(
                text=f"▶▶ Запустить все слоты подряд (#1→#{n_slots})",
                callback_data=f"proj:{project.id}:enrich_run_all",
            )
        ])

    # «➕ Добавить слот» — рисуется только пока не достигли лимита.
    if n_slots < MAX_ENRICH_SLOTS:
        rows.append([
            InlineKeyboardButton(
                text="➕ Добавить слот",
                callback_data=f"proj:{project.id}:enrich_add_slot",
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
        f"📁 Проект #{project.id} «{(project.topic or '').strip() or project.slug}»\n"
        f"slug: <code>{project.slug}</code>\n"
        f"hero: {project.hero_mode}\n"
        f"статус: <b>{project.status.value}</b>\n"
        f"{settings_line}"
    )
