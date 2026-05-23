"""Универсальные inline-клавиатуры и helpers для callback_data.

Используется новыми handler'ами (`app/telegram/handlers/*.py`). bot.py
переедет сюда постепенно (серия E.4 PR'ов).

Гарантии:
- Все возвращаемые клавиатуры собраны из `CB.X.value` (никаких литералов).
- `make_callback()` проверяет 64-байтный лимит Telegram до отправки.
- Кнопки «Назад» / «В меню» всегда в одном ряду, в одном порядке.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.telegram.callback_registry import CB, TG_CALLBACK_LIMIT

# ────────────────────────────────────────────────────────────────────────────
# Низкоуровневые helpers
# ────────────────────────────────────────────────────────────────────────────


def make_callback(prefix: CB, *parts: int | str) -> str:
    """Безопасно собрать callback_data из CB-префикса и переменных.

    Проверяет 64-байтный лимит на этапе сборки → ловим переполнения в
    тестах, а не в проде через BadRequest.

    >>> make_callback(CB.PROJ_MENU, 42, "menu")
    'proj:42:menu'
    >>> make_callback(CB.AI_APPROVE, 123)
    'ai:approve:123'
    """
    pieces = [prefix.value] + [str(p) for p in parts]
    result = ":".join(pieces)
    n = len(result.encode("utf-8"))
    if n > TG_CALLBACK_LIMIT:
        raise ValueError(
            f"callback_data {result!r} занимает {n} байт, лимит Telegram = "
            f"{TG_CALLBACK_LIMIT}. Сократи префикс или укоротите значения."
        )
    return result


# ────────────────────────────────────────────────────────────────────────────
# Стандартные кнопки и ряды (AGENTS.md §10: «у каждого экрана Назад/В меню»)
# ────────────────────────────────────────────────────────────────────────────


def btn_back_to_menu(text: str = "⬅ В меню") -> InlineKeyboardButton:
    """Кнопка "В главное меню"."""
    return InlineKeyboardButton(text=text, callback_data=CB.MENU_ROOT.value)


def btn_my_projects(text: str = "📋 Мои проекты") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=CB.MENU_LIST.value)


def btn_new_project(text: str = "📁 Новый проект") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=CB.MENU_NEW.value)


def row_back_menu(
    *,
    back_callback: str | None = None,
    include_menu: bool = True,
) -> list[InlineKeyboardButton]:
    """Стандартный нижний ряд экрана: «Назад» (опционально) + «В меню».

    Если у тебя есть свой back_callback (например, на родительский экран) —
    передай его. Иначе будет только «В меню».
    """
    row: list[InlineKeyboardButton] = []
    if back_callback:
        n = len(back_callback.encode("utf-8"))
        if n > TG_CALLBACK_LIMIT:
            raise ValueError(
                f"back_callback {back_callback!r} = {n} байт > {TG_CALLBACK_LIMIT}"
            )
        row.append(InlineKeyboardButton(text="⬅ Назад", callback_data=back_callback))
    if include_menu:
        row.append(btn_back_to_menu())
    return row


# ────────────────────────────────────────────────────────────────────────────
# Готовые клавиатуры
# ────────────────────────────────────────────────────────────────────────────


def kb_back_to_main() -> InlineKeyboardMarkup:
    """Только одна кнопка — «В главное меню». Для тупиковых экранов."""
    return InlineKeyboardMarkup(inline_keyboard=[[btn_back_to_menu()]])


def kb_yes_no(
    *,
    yes_callback: str,
    no_callback: str,
    yes_text: str = "✅ Да",
    no_text: str = "❌ Нет",
) -> InlineKeyboardMarkup:
    """Стандартная подтверждение/отмена клавиатура (2 кнопки в ряд).

    Используется для diff/confirm-карточек (например, «удалить файл?»,
    «применить правку?», и т.п.).
    """
    for cb in (yes_callback, no_callback):
        n = len(cb.encode("utf-8"))
        if n > TG_CALLBACK_LIMIT:
            raise ValueError(f"callback {cb!r} = {n} байт > {TG_CALLBACK_LIMIT}")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=yes_text, callback_data=yes_callback),
                InlineKeyboardButton(text=no_text, callback_data=no_callback),
            ]
        ]
    )


def kb_hitl_4buttons(
    *,
    approve_cb: str,
    regen_cb: str,
    clarify_cb: str,
    reject_cb: str,
    approve_text: str = "✅ Применить",
    regen_text: str = "🔁 Перегенерить",
    clarify_text: str = "✏️ Уточнить",
    reject_text: str = "❌ Отменить",
) -> InlineKeyboardMarkup:
    """Стандартная HITL-карточка с 4 кнопками: ✅/🔁/✏️/❌ в 2 ряда.

    Инвариант AGENTS.md §10: ВСЕ HITL карточки (картинки, видео, AI-агент
    edit_file, batch reviews) обязаны иметь ровно эти 4 кнопки.
    """
    for cb in (approve_cb, regen_cb, clarify_cb, reject_cb):
        n = len(cb.encode("utf-8"))
        if n > TG_CALLBACK_LIMIT:
            raise ValueError(f"HITL callback {cb!r} = {n} байт > {TG_CALLBACK_LIMIT}")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=approve_text, callback_data=approve_cb),
                InlineKeyboardButton(text=regen_text, callback_data=regen_cb),
            ],
            [
                InlineKeyboardButton(text=clarify_text, callback_data=clarify_cb),
                InlineKeyboardButton(text=reject_text, callback_data=reject_cb),
            ],
        ]
    )


def kb_session_summary(
    *,
    cancel_callback: str,
    status_callback: str,
    cancel_text: str = "⏹ Отменить",
    status_text: str = "📊 Status",
) -> InlineKeyboardMarkup:
    """Клавиатура «активная сессия»: cancel + status в одну строку.

    Используется для прогресс-сообщений (AI-агент, длинные шаги).
    """
    for cb in (cancel_callback, status_callback):
        n = len(cb.encode("utf-8"))
        if n > TG_CALLBACK_LIMIT:
            raise ValueError(f"summary callback {cb!r} = {n} байт > {TG_CALLBACK_LIMIT}")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=cancel_text, callback_data=cancel_callback),
                InlineKeyboardButton(text=status_text, callback_data=status_callback),
            ]
        ]
    )
