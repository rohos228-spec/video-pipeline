"""Фабрики клавиатур экрана проекта (Phase E.4 step 3 foundation).

Не модифицирует bot.py — новые фабрики для будущей миграции
`_project_menu_*` / `_step_run_kb` / etc.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.telegram.callback_registry import CB
from app.telegram.keyboards.common import make_callback, row_back_menu


def kb_project_menu(
    project_id: int,
    *,
    current_step: str | None = None,
    can_run: bool = True,
    can_stop: bool = False,
    can_excel: bool = True,
) -> InlineKeyboardMarkup:
    """Меню проекта: запустить шаг / прогнать с нуля / остановить / статус.

    AGENTS.md §10 invariant: должно содержать «Назад», «В меню»,
    «▶ Запустить шаг», «🔁 Прогнать с нуля», «⏹ Остановить»,
    «📊 Статус», «📁 Excel».
    """
    rows: list[list[InlineKeyboardButton]] = []

    if can_run and current_step:
        rows.append([
            InlineKeyboardButton(
                text=f"▶ Запустить шаг: {current_step}",
                callback_data=make_callback(CB.STEP_RUN, project_id, current_step),
            )
        ])
        rows.append([
            InlineKeyboardButton(
                text="🔁 Прогнать шаг с нуля",
                callback_data=make_callback(CB.RESET_ASK, project_id, current_step),
            )
        ])

    if can_stop:
        rows.append([
            InlineKeyboardButton(
                text="⏹ Остановить текущий шаг",
                callback_data=make_callback(CB.PROJ_MENU, project_id, "stop_running"),
            )
        ])

    if can_excel:
        rows.append([
            InlineKeyboardButton(
                text="📁 Excel-снимок",
                callback_data=make_callback(CB.PROJ_MENU, project_id, "excel"),
            )
        ])

    # Назад / В меню
    rows.append(row_back_menu(
        back_callback=make_callback(CB.PROJ_MENU, project_id, "menu"),
    ))

    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_project_delete_confirm(project_id: int) -> InlineKeyboardMarkup:
    """Подтверждение удаления проекта: ❌ Удалить безвозвратно / ↩ Отмена."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Удалить безвозвратно",
                    callback_data=make_callback(CB.PROJ_MENU, project_id, "delete_yes"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↩ Отмена",
                    callback_data=make_callback(CB.PROJ_MENU, project_id, "menu"),
                ),
            ],
        ]
    )


def kb_reset_step_confirm(project_id: int, step_code: str) -> InlineKeyboardMarkup:
    """Подтверждение reset шага: ✅ Да, прогнать заново / ↩ Отмена."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить и прогнать заново",
                    callback_data=make_callback(CB.RESET_DO, project_id, step_code),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↩ Отмена",
                    callback_data=make_callback(CB.PROJ_MENU, project_id, "menu"),
                ),
            ],
        ]
    )


__all__ = [
    "kb_project_delete_confirm",
    "kb_project_menu",
    "kb_reset_step_confirm",
]
