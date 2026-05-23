"""Фабрики клавиатур для wizard'а (Phase E.4 step 7 foundation).

Wizard в видеоконвейере — это пятишаговая настройка нового проекта /
массового batch'а (5 вопросов: video_generator, aspect_ratio, и т.д.).

Сейчас все клавиатуры собираются в `app/telegram/wizard.py` руками. Эти
фабрики — типизированный foundation для будущей миграции.

См. AGENTS.md §10: на каждом экране wizard'а должно быть «← Назад на 1 шаг»
и «❌ Отмена».
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.telegram.callback_registry import CB
from app.telegram.keyboards.common import make_callback


def kb_wizard_start(batch_id: int) -> InlineKeyboardMarkup:
    """Стартовый экран wizard'а: ⚙ Заполнить настройки / ↻ Сбросить."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⚙ Заполнить настройки (5 вопросов)",
                    callback_data=make_callback(CB.WIZ, batch_id, "start"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↻ Сбросить настройки",
                    callback_data=make_callback(CB.WIZ, batch_id, "reset"),
                ),
            ],
        ]
    )


def kb_wizard_choice(
    batch_id: int,
    field: str,
    options: list[tuple[str, str]],
    *,
    back_callback: str | None = None,
    add_cancel: bool = True,
) -> InlineKeyboardMarkup:
    """Выбор значения для одного wizard-поля.

    Args:
        batch_id: id массового batch'а.
        field: имя поля (например 'video_generator', 'aspect_ratio').
        options: список (label, value) — кнопки на выбор.
        back_callback: callback для «← Назад на 1 шаг» (опционально).
        add_cancel: добавить «❌ Отмена» (по умолчанию True).

    Каждый callback: 'wiz:{batch_id}:set:{field}:{value}'.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for label, value in options:
        rows.append([
            InlineKeyboardButton(
                text=label,
                callback_data=make_callback(CB.WIZ, batch_id, "set", field, value),
            )
        ])

    nav_row: list[InlineKeyboardButton] = []
    if back_callback:
        nav_row.append(
            InlineKeyboardButton(text="← Назад на 1 шаг", callback_data=back_callback)
        )
    if add_cancel:
        nav_row.append(
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=make_callback(CB.WIZ, batch_id, "cancel"),
            )
        )
    if nav_row:
        rows.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_wizard_confirm(batch_id: int) -> InlineKeyboardMarkup:
    """Финальное подтверждение wizard'а: ✅ Применить настройки / ↩ Отмена."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Применить настройки",
                    callback_data=make_callback(CB.WIZ, batch_id, "apply"),
                ),
                InlineKeyboardButton(
                    text="↩ Отмена",
                    callback_data=make_callback(CB.WIZ, batch_id, "cancel"),
                ),
            ],
        ]
    )


__all__ = [
    "kb_wizard_choice",
    "kb_wizard_confirm",
    "kb_wizard_start",
]
