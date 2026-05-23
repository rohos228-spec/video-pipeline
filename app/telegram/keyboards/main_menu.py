"""Фабрики клавиатур главного меню (Phase E.4 step 3 foundation).

Не модифицирует bot.py — это новые фабрики которые handler'ы могут
использовать вместо собственноручной сборки.

Когда будет миграция bot.py — мини-PR'ы заменят inline-сборку в
`menu.py` / `bot.py:_main_menu_*` на вызовы этих фабрик.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.telegram.callback_registry import CB


def kb_main_menu(
    *,
    show_ai_agent: bool = True,
    show_debug: bool = True,
) -> InlineKeyboardMarkup:
    """Главное меню бота с базовым набором кнопок.

    AGENTS.md §10 invariant: главное меню должно содержать
    `🎬 Новый`, `📋 Мои проекты`, `🎬 Массовое создание`,
    `🧪 Тест промтов`, `🔬 Visual Lab` (если активна),
    `🤖 ИИ-агент`, `⚙ Настройки`, `🩺 Debug`.

    Параметры (для будущих расширений):
        show_ai_agent — добавить кнопку '🤖 ИИ-агент'.
        show_debug — добавить '🩺 Debug' (только для owner).
    """
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="📁 Новый проект", callback_data=CB.MENU_NEW.value),
            InlineKeyboardButton(text="📋 Мои проекты", callback_data=CB.MENU_LIST.value),
        ],
        [
            InlineKeyboardButton(text="🎬 Массовое создание", callback_data=CB.MASS_LIST.value),
            InlineKeyboardButton(text="🧪 Тест промтов", callback_data=CB.TEST_LIST.value),
        ],
    ]
    if show_ai_agent:
        # AI-агент — без callback_data, потому что вход через text-command /ai.
        # Кнопка-плашка с инструкцией: callback noop.
        rows.append([
            InlineKeyboardButton(text="🤖 ИИ-агент (/ai)", callback_data=CB.NOOP.value),
        ])
    if show_debug:
        rows.append([
            InlineKeyboardButton(text="🩺 /debug", callback_data=CB.NOOP.value),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_mass_pause_resume(*, paused: bool) -> InlineKeyboardMarkup:
    """Кнопка управления глобальной массовой паузой (все batch'и).

    Используется в главном меню если есть активные batch'и:
    paused=True → '▶ Возобновить массовую' (callback menu:mresume)
    paused=False → '⏸ Пауза массовой'    (callback menu:mpause)
    """
    if paused:
        btn = InlineKeyboardButton(
            text="▶ Возобновить массовую (снять паузу)",
            callback_data=CB.MENU_MASS_RESUME.value,
        )
    else:
        btn = InlineKeyboardButton(
            text="⏸ Пауза массовой (все батчи)",
            callback_data=CB.MENU_MASS_PAUSE.value,
        )
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


__all__ = [
    "kb_main_menu",
    "kb_mass_pause_resume",
]
