"""Мастер настроек проекта: несколько вопросов после создания проекта.

Сценарий:
  1. /new → пользователь вводит название → создаётся проект в статусе `new`
  2. Бот запускает мастер: Q1 (image generator) + картинка-превью
  3. Юзер жмёт кнопку → callback `wiz:<pid>:set:image_generator:<id>`
     → сохраняем в Project.image_generator → показываем Q2
  4. …Q2 (aspect ratio) …Q3 (image res) …Q4 (image_relax) …Q5 (video gen)
     …Q6 (video res) …Q7 (video_relax — только для veo-3-1-fast)
  5. После последнего — показываем обычное меню проекта.

Если юзер не ответил на все вопросы, проект висит в `new` — воркер его не трогает,
шаги в меню заблокированы. Кнопка «⚙ Настройки» в меню проекта позволяет
перезапустить мастер (или изменить отдельное поле).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from aiogram import Bot
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from loguru import logger
from sqlalchemy import select

from app.db import session_scope
from app.generation_options import (
    ASPECT_RATIOS,
    ASPECT_RATIOS_BY_ID,
    IMAGE_GENERATORS,
    IMAGE_GENERATORS_BY_ID,
    IMAGE_QUALITIES,
    IMAGE_QUALITIES_BY_ID,
    IMAGE_RESOLUTIONS,
    IMAGE_RESOLUTIONS_BY_ID,
    OptionChoice,
    VIDEO_GENERATORS,
    VIDEO_GENERATORS_BY_ID,
    VIDEO_RESOLUTIONS,
    VIDEO_RESOLUTIONS_BY_ID,
    is_gpt_image_generator,
)
from app.models import Project

# Корень репо (для картинок-референсов). Файл app/telegram/wizard.py →
# app/telegram → app → repo. repo/assets/reference/*.png
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_REF_DIR = _REPO_ROOT / "assets" / "reference"

_IMG_GENERATORS_REF = _REF_DIR / "image_generators.png"
_ASPECT_REF = _REF_DIR / "aspect_ratios.png"
_VIDEO_GENERATORS_REF = _REF_DIR / "video_generators.png"


# ---- Порядок полей и каталог кнопок ----------------------------------------

# Yes/No-выбор для булевых полей (Relax). id — 'yes'/'no'.
BOOLEAN_CHOICES: list[OptionChoice] = [
    OptionChoice("yes", "Да", "yes", "Включить режим"),
    OptionChoice("no", "Нет", "no", "Оставить выключенным"),
]
BOOLEAN_CHOICES_BY_ID = {c.id: c for c in BOOLEAN_CHOICES}


@dataclass(frozen=True)
class WizardQuestion:
    field: str
    title: str
    choices: list[OptionChoice]
    image_path: Path | None
    cols: int
    # Каталог вариантов (для валидации при set).
    catalog: dict[str, OptionChoice]
    # Как преобразовать id варианта в реальное значение для колонки бд.
    to_db: Callable[[str], object] = lambda x: x
    # Был ли вопрос уже отвечен. По умолчанию — проверяем
    # «значение не None и не пустая строка». Для boolean-полей — «не None».
    is_set: Callable[[Project], bool] = lambda p: getattr(p, "", None) not in (None, "")
    # Скип вопроса для конкретного проекта (не применим).
    skip_if: Callable[[Project], bool] = lambda p: False
    # Что ставить в db если вопрос скипнут (иначе останется None → луп).
    skip_value: object = False


def _is_set_str(field: str) -> Callable[[Project], bool]:
    return lambda p: getattr(p, field, None) not in (None, "")


def _is_set_bool(field: str) -> Callable[[Project], bool]:
    return lambda p: getattr(p, field, None) is not None


_QUESTIONS: list[WizardQuestion] = [
    WizardQuestion(
        field="image_generator",
        title="1/8. Какой <b>генератор картинок</b> использовать?",
        choices=IMAGE_GENERATORS,
        image_path=_IMG_GENERATORS_REF,
        cols=1,
        catalog=IMAGE_GENERATORS_BY_ID,
        is_set=_is_set_str("image_generator"),
    ),
    WizardQuestion(
        field="aspect_ratio",
        title="2/8. Какое <b>соотношение сторон</b> картинок?",
        choices=ASPECT_RATIOS,
        image_path=_ASPECT_REF,
        cols=4,
        catalog=ASPECT_RATIOS_BY_ID,
        is_set=_is_set_str("aspect_ratio"),
    ),
    WizardQuestion(
        field="image_resolution",
        title="3/8. <b>Разрешение картинки</b>?",
        choices=IMAGE_RESOLUTIONS,
        image_path=None,
        cols=3,
        catalog=IMAGE_RESOLUTIONS_BY_ID,
        is_set=_is_set_str("image_resolution"),
    ),
    WizardQuestion(
        field="image_quality",
        title="4/8. <b>Качество картинки</b>? (GPT Image)",
        choices=IMAGE_QUALITIES,
        image_path=None,
        cols=3,
        catalog=IMAGE_QUALITIES_BY_ID,
        is_set=_is_set_str("image_quality"),
        skip_if=lambda p: not is_gpt_image_generator(p.image_generator),
        skip_value="medium",
    ),
    WizardQuestion(
        field="image_relax",
        title=(
            "5/8. <b>Relax-режим картинок</b>?\n"
            "Если «Да» — outsee включит «Безлимит» перед генерацией."
        ),
        choices=BOOLEAN_CHOICES,
        image_path=None,
        cols=2,
        catalog=BOOLEAN_CHOICES_BY_ID,
        to_db=lambda v: v == "yes",
        is_set=_is_set_bool("image_relax"),
    ),
    WizardQuestion(
        field="video_generator",
        title="6/8. Какой <b>видео-генератор</b> использовать?",
        choices=VIDEO_GENERATORS,
        image_path=_VIDEO_GENERATORS_REF,
        cols=1,
        catalog=VIDEO_GENERATORS_BY_ID,
        is_set=_is_set_str("video_generator"),
    ),
    WizardQuestion(
        field="video_resolution",
        title="7/8. <b>Разрешение видео</b>?",
        choices=VIDEO_RESOLUTIONS,
        image_path=None,
        cols=2,
        catalog=VIDEO_RESOLUTIONS_BY_ID,
        is_set=_is_set_str("video_resolution"),
    ),
    WizardQuestion(
        field="video_relax",
        title=(
            "8/8. <b>Relax-режим видео</b>?\n"
            "Поддерживается только для Veo 3.1 Fast."
        ),
        choices=BOOLEAN_CHOICES,
        image_path=None,
        cols=2,
        catalog=BOOLEAN_CHOICES_BY_ID,
        to_db=lambda v: v == "yes",
        is_set=_is_set_bool("video_relax"),
        skip_if=lambda p: (p.video_generator or "") != "veo_3_1_fast",
        skip_value=False,
    ),
]


_QUESTIONS_BY_FIELD: dict[str, WizardQuestion] = {q.field: q for q in _QUESTIONS}


def _wizard_step_index(project: Project) -> int:
    """Возвращает индекс следующего НЕ заполненного вопроса или len(_QUESTIONS)
    если все заполнены. Вопросы, попавшие под skip_if, считаются заполненными."""
    for i, q in enumerate(_QUESTIONS):
        if q.skip_if(project):
            continue
        if not q.is_set(project):
            return i
    return len(_QUESTIONS)


def is_wizard_complete(project: Project) -> bool:
    return _wizard_step_index(project) >= len(_QUESTIONS)


def _kb_for_question(
    project_id: int, field: str, choices: list[OptionChoice], cols: int
) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for ch in choices:
        row.append(
            InlineKeyboardButton(
                text=ch.label,
                callback_data=f"wiz:{project_id}:set:{field}:{ch.id}",
            )
        )
        if len(row) >= cols:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    # Кнопка «⬅ Отмена / В меню» не нужна на первом вопросе — проект только
    # создан. Но полезна когда юзер пересматривает настройки.
    buttons.append(
        [
            InlineKeyboardButton(
                text="⬅ В меню проекта", callback_data=f"proj:{project_id}:menu"
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def send_wizard_question(bot: Bot, chat_id: int, project: Project) -> None:
    """Шлёт очередной вопрос мастера для указанного проекта. Если все
    отвечены — присылает финальную сводку + кнопку «В меню проекта»."""
    idx = _wizard_step_index(project)
    if idx >= len(_QUESTIONS):
        await _send_wizard_complete(bot, chat_id, project)
        return
    q = _QUESTIONS[idx]
    field, title, choices, image_path, cols = q.field, q.title, q.choices, q.image_path, q.cols
    kb = _kb_for_question(project.id, field, choices, cols)

    # Текст — вопрос + список пояснений (чтобы юзер видел описания моделей
    # помимо кнопок).
    lines = [title, ""]
    for ch in choices:
        if ch.short_desc:
            lines.append(f"• <b>{ch.label}</b> — {ch.short_desc}")
        else:
            lines.append(f"• <b>{ch.label}</b>")
    body = "\n".join(lines)

    if image_path is not None and image_path.exists():
        # Фото с подписью. TG caption limit = 1024 символа.
        caption = body if len(body) <= 1000 else (body[:997] + "…")
        try:
            await bot.send_photo(
                chat_id,
                FSInputFile(str(image_path)),
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb if len(body) <= 1000 else None,
            )
            if len(body) > 1000:
                tail = body[1000:]
                await bot.send_message(
                    chat_id, tail, parse_mode="HTML", reply_markup=kb
                )
            return
        except Exception as e:  # noqa: BLE001
            logger.warning("wizard: send_photo failed, fallback to text: {}", e)
    # Фоллбэк — только текст.
    await bot.send_message(chat_id, body, parse_mode="HTML", reply_markup=kb)


async def _send_wizard_complete(bot: Bot, chat_id: int, project: Project) -> None:
    from app.telegram.menu import project_header, project_menu_kb

    await bot.send_message(
        chat_id,
        "✅ Настройки проекта сохранены. Теперь можно запускать шаги.\n\n"
        + project_header(project),
        parse_mode="HTML",
        reply_markup=project_menu_kb(project),
    )


# ---------------------------------------------------------------------------
# Подменю «⛙ Настройки» для уже пройденного мастера: поле-по-полю
# редактирование. Чтобы поменять одну модель (например видео-генератор
# с kling_2_6 на veo_3_fast) — не нужно сбрасывать все 7 полей, кликаем прямо
# по нужному. Роутинг:
#   «⛙ Настройки» в меню проекта → wiz:<pid>:start →
#     - если мастер НЕ пройден: send_wizard_question (старый флоу)
#     - если мастер пройден: _send_settings_overview (новый флоу)
#   Клик по полю в overview → wiz:<pid>:edit:<field> → пикер с callback
#   wiz:<pid>:setone:<field>:<option_id>. После setone — возврат в overview
#   (в отличие от set, который ведёт к следующему вопросу мастера).

_FIELD_LABELS: dict[str, str] = {
    "image_generator": "🖌 Генератор картинок",
    "aspect_ratio": "📐 Соотношение сторон",
    "image_resolution": "🖼️ Разрешение картинки",
    "image_quality": "✨ Качество картинки",
    "image_relax": "⏱ Relax картинок",
    "video_generator": "🎬 Видео-генератор",
    "video_resolution": "📺 Разрешение видео",
    "video_relax": "⏱ Relax видео",
}


def _current_value_label(project: Project, q: WizardQuestion) -> str:
    """Человеческое имя текущего значения поля: '—' / 'Да' / 'Нет' / label
    из каталога. Для boolean-полей (`image_relax` / `video_relax`)
    хранится уже bool, в каталоге лежит 'yes'/'no' — конвертируем."""
    val = getattr(project, q.field, None)
    if val is None:
        return "—"
    if q.field in ("image_relax", "video_relax"):
        return "Да" if val else "Нет"
    choice = q.catalog.get(str(val))
    if choice is None:
        return str(val)
    return choice.label


def _settings_overview_text(project: Project) -> str:
    topic = project.topic or project.slug
    lines = [
        f"<b>⛙ Настройки проекта #{project.id}</b>",
        f"«{topic}»",
        "",
        "Кликни любое поле — поменяешь только его, остальные "
        "не сбросятся.",
        "",
    ]
    for q in _QUESTIONS:
        if q.skip_if(project):
            # Поле неприменимо для текущего проекта (например video_relax
            # доступен только для veo_3_1_fast). Прячем из overview.
            continue
        lines.append(
            f"• {_FIELD_LABELS.get(q.field, q.field)}: "
            f"<b>{_current_value_label(project, q)}</b>"
        )
    return "\n".join(lines)


def _settings_overview_kb(project: Project) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for q in _QUESTIONS:
        if q.skip_if(project):
            continue
        rows.append([
            InlineKeyboardButton(
                text=(
                    f"✏ {_FIELD_LABELS.get(q.field, q.field)}: "
                    f"{_current_value_label(project, q)}"
                ),
                callback_data=f"wiz:{project.id}:edit:{q.field}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="↻ Сбросить все настройки",
            callback_data=f"wiz:{project.id}:reset",
        ),
    ])
    rows.append([
        InlineKeyboardButton(
            text="⬅ В меню проекта",
            callback_data=f"proj:{project.id}:menu",
        ),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_settings_overview(
    bot: Bot, chat_id: int, project: Project
) -> None:
    await bot.send_message(
        chat_id,
        _settings_overview_text(project),
        parse_mode="HTML",
        reply_markup=_settings_overview_kb(project),
    )


def _kb_for_edit(
    project_id: int, field: str, choices: list[OptionChoice], cols: int
) -> InlineKeyboardMarkup:
    """Пикер редактирования одного поля. Callback:
    `wiz:<pid>:setone:<field>:<option_id>`. После клика — возврат в overview.
    """
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for ch in choices:
        row.append(
            InlineKeyboardButton(
                text=ch.label,
                callback_data=f"wiz:{project_id}:setone:{field}:{ch.id}",
            )
        )
        if len(row) >= cols:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton(
            text="⬅ Назад в настройки",
            callback_data=f"wiz:{project_id}:start",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _send_edit_picker(
    bot: Bot, chat_id: int, project: Project, field: str
) -> None:
    """Показать пикер выбора для одного поля. Использует те же
    `choices` и `image_path`, что и мастер, но callback'и ведут на setone.
    """
    question = _QUESTIONS_BY_FIELD.get(field)
    if question is None:
        await bot.send_message(chat_id, f"wizard: неизвестное поле {field}")
        return
    kb = _kb_for_edit(project.id, field, question.choices, question.cols)
    current = _current_value_label(project, question)
    lines = [
        f"<b>Меняем:</b> {_FIELD_LABELS.get(field, field)}",
        f"<b>Сейчас:</b> {current}",
        "",
        question.title,
        "",
    ]
    for ch in question.choices:
        if ch.short_desc:
            lines.append(f"• <b>{ch.label}</b> — {ch.short_desc}")
        else:
            lines.append(f"• <b>{ch.label}</b>")
    body = "\n".join(lines)
    image_path = question.image_path
    if image_path is not None and image_path.exists():
        caption = body if len(body) <= 1000 else (body[:997] + "…")
        try:
            await bot.send_photo(
                chat_id,
                FSInputFile(str(image_path)),
                caption=caption,
                parse_mode="HTML",
                reply_markup=kb if len(body) <= 1000 else None,
            )
            if len(body) > 1000:
                tail = body[1000:]
                await bot.send_message(
                    chat_id, tail, parse_mode="HTML", reply_markup=kb
                )
            return
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "wizard: send_photo failed in edit picker: {}", e
            )
    await bot.send_message(chat_id, body, parse_mode="HTML", reply_markup=kb)


async def handle_wizard_callback(cb: CallbackQuery) -> None:
    """Обрабатывает `wiz:<pid>:set:<field>:<option_id>` и
    `wiz:<pid>:start`.

    Зарегистрирован в bot.py (через F.data.regexp(r'^wiz:')).
    """
    data = cb.data or ""
    parts = data.split(":")
    # Форматы:
    #   wiz:<pid>:start                       — показать текущий вопрос
    #   wiz:<pid>:set:<field>:<option_id>     — установить значение → next
    #   wiz:<pid>:reset                       — сбросить все 5 полей
    if len(parts) < 3:
        await cb.answer("wizard: плохой callback", show_alert=True)
        return
    try:
        project_id = int(parts[1])
    except ValueError:
        await cb.answer("wizard: плохой pid", show_alert=True)
        return
    action = parts[2]

    if action == "start":
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == project_id))
            ).scalar_one_or_none()
            if project is None:
                await cb.answer("Проект не найден", show_alert=True)
                return
            wiz_done = is_wizard_complete(project)
        await cb.answer()
        if wiz_done:
            # Мастер уже пройден — показываем подменю «Ячеика-по-ячейке»,
            # из которого юзер может поменять любое одно поле.
            await _send_settings_overview(cb.bot, cb.message.chat.id, project)
        else:
            await send_wizard_question(cb.bot, cb.message.chat.id, project)
        return

    if action == "edit" and len(parts) >= 4:
        field = parts[3]
        if field not in _QUESTIONS_BY_FIELD:
            await cb.answer(
                f"wizard: неизвестное поле {field}", show_alert=True
            )
            return
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == project_id))
            ).scalar_one_or_none()
            if project is None:
                await cb.answer("Проект не найден", show_alert=True)
                return
        await cb.answer()
        await _send_edit_picker(cb.bot, cb.message.chat.id, project, field)
        return

    if action == "setone" and len(parts) >= 5:
        # Аналог set, но после сохранения возвращаем юзера в overview
        # настроек, а не в «следующий вопрос мастера». Используется,
        # когда юзер редактирует одно поле через «⛙ Настройки» уже после
        # полного прохождения мастера.
        field = parts[3]
        option_id = parts[4]
        question = _QUESTIONS_BY_FIELD.get(field)
        if question is None:
            await cb.answer(
                f"wizard: неизвестное поле {field}", show_alert=True
            )
            return
        choice = question.catalog.get(option_id)
        if choice is None:
            await cb.answer(
                f"wizard: неизвестный вариант {option_id}", show_alert=True
            )
            return
        db_value = question.to_db(option_id)

        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == project_id))
            ).scalar_one_or_none()
            if project is None:
                await cb.answer("Проект не найден", show_alert=True)
                return
            setattr(project, field, db_value)
            # Если зависимые поля теперь попадают под skip_if (например
            # сменили video_generator с veo_3_1_fast на другой — video_relax
            # теперь не применим) и в них лежит None — выставляем skip_value.
            # Без этого is_wizard_complete вернёт False, хотя по логике всё
            # заполнено.
            for q in _QUESTIONS:
                if q.field == field:
                    continue
                if q.skip_if(project) and not q.is_set(project):
                    setattr(project, q.field, q.skip_value)
            # Зеркалим в xlsx (general-лист), чтобы пользователь видел настройки.
            try:
                from app.storage import for_project as _sheet_for_project

                _sheet_for_project(project).write_general(
                    **{field: choice.label}
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "wizard: xlsx write failed ({}): {}", field, e
                )
            await s.flush()
            await s.refresh(project)
            await s.commit()
            snap = Project(
                id=project.id,
                slug=project.slug,
                topic=project.topic,
                hero_mode=project.hero_mode,
                status=project.status,
                image_generator=project.image_generator,
                aspect_ratio=project.aspect_ratio,
                image_resolution=project.image_resolution,
                image_quality=project.image_quality,
                image_relax=project.image_relax,
                video_generator=project.video_generator,
                video_resolution=project.video_resolution,
                video_relax=project.video_relax,
            )

        await cb.answer(f"{choice.label} ✓")
        await _send_settings_overview(cb.bot, cb.message.chat.id, snap)
        return

    if action == "reset":
        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == project_id))
            ).scalar_one_or_none()
            if project is None:
                await cb.answer("Проект не найден", show_alert=True)
                return
            project.image_generator = None
            project.aspect_ratio = None
            project.image_resolution = None
            project.image_quality = None
            project.image_relax = None
            project.video_generator = None
            project.video_resolution = None
            project.video_relax = None
            await s.flush()
            # Перечитаем для отправки next question
            await s.refresh(project)
            proj_copy = project
            await s.commit()
        await cb.answer("Настройки сброшены")
        await send_wizard_question(cb.bot, cb.message.chat.id, proj_copy)
        return

    if action == "set" and len(parts) >= 5:
        field = parts[3]
        option_id = parts[4]
        question = _QUESTIONS_BY_FIELD.get(field)
        if question is None:
            await cb.answer(f"wizard: неизвестное поле {field}", show_alert=True)
            return
        choice = question.catalog.get(option_id)
        if choice is None:
            await cb.answer(
                f"wizard: неизвестный вариант {option_id}", show_alert=True
            )
            return
        db_value = question.to_db(option_id)

        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == project_id))
            ).scalar_one_or_none()
            if project is None:
                await cb.answer("Проект не найден", show_alert=True)
                return
            setattr(project, field, db_value)
            # Если следующие вопросы попадают под skip_if (стали неприменимыми
            # из-за только что изменённого поля) — выставляем skip_value, чтобы
            # мастер их пропускал вперёд.
            for q in _QUESTIONS:
                if q.field == field:
                    continue
                if q.skip_if(project) and not q.is_set(project):
                    setattr(project, q.field, q.skip_value)
            # Зеркалим в xlsx (general-лист), чтобы пользователь видел настройки.
            try:
                from app.storage import for_project as _sheet_for_project

                _sheet_for_project(project).write_general(
                    **{field: choice.label}
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("wizard: xlsx write failed ({}): {}", field, e)
            await s.flush()
            await s.refresh(project)
            await s.commit()
            # Для повторного чтения после commit — возьмём копию «снимком»
            snap = Project(
                id=project.id,
                slug=project.slug,
                topic=project.topic,
                hero_mode=project.hero_mode,
                status=project.status,
                image_generator=project.image_generator,
                aspect_ratio=project.aspect_ratio,
                image_resolution=project.image_resolution,
                image_quality=project.image_quality,
                image_relax=project.image_relax,
                video_generator=project.video_generator,
                video_resolution=project.video_resolution,
                video_relax=project.video_relax,
            )

        await cb.answer(f"{choice.label} ✓")
        await send_wizard_question(cb.bot, cb.message.chat.id, snap)
        return

    await cb.answer("wizard: неизвестное действие", show_alert=True)
