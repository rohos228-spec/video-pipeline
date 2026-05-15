"""Меню для «Тестирование визуальных промтов».

Структура callback'ов: `test:<verb>` или `test:<id>:<verb>`.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.models import TestPromptProject


def test_root_kb(projects: list[TestPromptProject]) -> InlineKeyboardMarkup:
    """Список тестовых проектов + «➕ Новый»."""
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text="➕ Новый тестовый проект",
            callback_data="test:new",
        )],
    ]
    for p in projects:
        rows.append([
            InlineKeyboardButton(
                text=(
                    f"#{p.id} {p.name[:40]} · iter={p.current_iter} · "
                    f"{p.status}"
                ),
                callback_data=f"test:{p.id}:menu",
            )
        ])
    rows.append([
        InlineKeyboardButton(text="⬅ Меню", callback_data="menu:root"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def test_project_kb(p: TestPromptProject) -> InlineKeyboardMarkup:
    """Меню одного тестового проекта."""
    has_visual = bool(p.visual_prompt)
    has_system = bool(p.system_prompt)

    rows: list[list[InlineKeyboardButton]] = []

    rows.append([
        InlineKeyboardButton(
            text=(
                f"📝 Визуальный промт {'✅' if has_visual else '❌'}"
            ),
            callback_data=f"test:{p.id}:set_visual",
        ),
    ])
    rows.append([
        InlineKeyboardButton(
            text=(
                f"⚙ Системный промт для GPT {'✅' if has_system else '❌'}"
            ),
            callback_data=f"test:{p.id}:set_system",
        ),
    ])

    busy = p.status in ("running_gpt", "running_outsee")
    waiting = p.status == "waiting_critique"

    if busy:
        rows.append([
            InlineKeyboardButton(
                text=f"⏳ Идёт шаг: {p.status}",
                callback_data="test:noop",
            ),
        ])
        rows.append([
            InlineKeyboardButton(
                text="🛑 Стоп",
                callback_data=f"test:{p.id}:stop",
            ),
        ])
    elif waiting:
        rows.append([
            InlineKeyboardButton(
                text="✏ Добавить критику и сгенерить ещё раз",
                callback_data=f"test:{p.id}:critique",
            ),
        ])
        rows.append([
            InlineKeyboardButton(
                text="🛑 Стоп цикла",
                callback_data=f"test:{p.id}:stop",
            ),
        ])
    else:
        # idle / stopped / error
        if has_visual and has_system:
            label = (
                "▶ Поехали" if p.current_iter == 0
                else "▶ Повторить с теми же промтами"
            )
            rows.append([
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"test:{p.id}:start",
                ),
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text="ℹ Задай оба промта чтобы запустить",
                    callback_data="test:noop",
                ),
            ])

    rows.append([
        InlineKeyboardButton(
            text="🗑 Удалить проект",
            callback_data=f"test:{p.id}:delete",
        ),
        InlineKeyboardButton(text="⬅ К списку", callback_data="test:list"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)
