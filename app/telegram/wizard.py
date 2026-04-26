"""Мастер настроек проекта: 5 вопросов после создания проекта.

Сценарий:
  1. /new → пользователь вводит тему → создаётся проект в статусе `new`
  2. Бот запускает мастер: Q1 (image generator) + картинка-превью
  3. Юзер жмёт кнопку → callback `wiz:<pid>:set:image_generator:<id>`
     → сохраняем в Project.image_generator → показываем Q2
  4. …Q2 (aspect ratio) …Q3 (image res) …Q4 (video gen) …Q5 (video res)
  5. После Q5 — показываем обычное меню проекта (step-кнопки 1…10)

Если юзер не ответил на Q1-Q5, проект висит в `new` — воркер его не трогает,
шаги в меню заблокированы. Кнопка «⚙ Настройки» в меню проекта позволяет
перезапустить мастер (или изменить отдельное поле).
"""

from __future__ import annotations

from pathlib import Path

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
    IMAGE_RESOLUTIONS,
    IMAGE_RESOLUTIONS_BY_ID,
    OptionChoice,
    VIDEO_GENERATORS,
    VIDEO_GENERATORS_BY_ID,
    VIDEO_RESOLUTIONS,
    VIDEO_RESOLUTIONS_BY_ID,
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

# Порядок вопросов: (поле Project, заголовок, варианты, картинка-путь,
# колонок в клавиатуре)
_QUESTIONS: list[tuple[str, str, list[OptionChoice], Path | None, int]] = [
    (
        "image_generator",
        "1/5. Какой <b>генератор картинок</b> использовать?",
        IMAGE_GENERATORS,
        _IMG_GENERATORS_REF,
        1,  # 1 кнопка в ряду — длинные названия
    ),
    (
        "aspect_ratio",
        "2/5. Какое <b>соотношение сторон</b> картинок?",
        ASPECT_RATIOS,
        _ASPECT_REF,
        4,  # 4 кнопки в ряду — короткие подписи (16:9 и т.д.)
    ),
    (
        "image_resolution",
        "3/5. <b>Разрешение картинки</b>?",
        IMAGE_RESOLUTIONS,
        None,
        2,
    ),
    (
        "video_generator",
        "4/5. Какой <b>видео-генератор</b> использовать?",
        VIDEO_GENERATORS,
        _VIDEO_GENERATORS_REF,
        1,
    ),
    (
        "video_resolution",
        "5/5. <b>Разрешение видео</b>?",
        VIDEO_RESOLUTIONS,
        None,
        2,
    ),
]


_CATALOGS = {
    "image_generator": IMAGE_GENERATORS_BY_ID,
    "aspect_ratio": ASPECT_RATIOS_BY_ID,
    "image_resolution": IMAGE_RESOLUTIONS_BY_ID,
    "video_generator": VIDEO_GENERATORS_BY_ID,
    "video_resolution": VIDEO_RESOLUTIONS_BY_ID,
}


def _wizard_step_index(project: Project) -> int:
    """Возвращает индекс следующего НЕ заполненного поля (0..4) или 5 если
    все заполнены."""
    for i, (field, *_rest) in enumerate(_QUESTIONS):
        if getattr(project, field, None) in (None, ""):
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
    field, title, choices, image_path, cols = _QUESTIONS[idx]
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
        await cb.answer()
        await send_wizard_question(cb.bot, cb.message.chat.id, project)
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
            project.video_generator = None
            project.video_resolution = None
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
        catalog = _CATALOGS.get(field)
        if catalog is None:
            await cb.answer(f"wizard: неизвестное поле {field}", show_alert=True)
            return
        choice = catalog.get(option_id)
        if choice is None:
            await cb.answer(
                f"wizard: неизвестный вариант {option_id}", show_alert=True
            )
            return

        async with session_scope() as s:
            project = (
                await s.execute(select(Project).where(Project.id == project_id))
            ).scalar_one_or_none()
            if project is None:
                await cb.answer("Проект не найден", show_alert=True)
                return
            setattr(project, field, option_id)
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
                video_generator=project.video_generator,
                video_resolution=project.video_resolution,
            )

        await cb.answer(f"{choice.label} ✓")
        await send_wizard_question(cb.bot, cb.message.chat.id, snap)
        return

    await cb.answer("wizard: неизвестное действие", show_alert=True)
