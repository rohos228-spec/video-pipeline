"""Фабрики HITL-карточек image / video (`hitl:*` callbacks).

Сейчас bot.py + services/hitl.py собирают HITL-кнопки руками. Этот модуль
даёт типизированные фабрики для постепенной миграции.

См. AGENTS.md §10 invariant: HITL-карточка обязана содержать
`✅ Одобрить`, `🔁 Перегенерировать`, `✏️ Изменить промт`, `❌ Отклонить`.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.telegram.callback_registry import CB
from app.telegram.keyboards.common import make_callback


def kb_hitl_image(
    hitl_id: int,
    *,
    allow_edit_prompt: bool = True,
    allow_original: bool = False,
) -> InlineKeyboardMarkup:
    """Стандартная HITL-карточка для одного кадра (image).

    Layout (2-3 ряда):
      ✅ Одобрить        🔁 Перегенерировать
      ✏️ Изменить промт  (опционально)
      📷 Оригинал        (опционально, для batch)
      ❌ Отклонить
    """
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="✅ Одобрить",
                callback_data=make_callback(CB.HITL, hitl_id, "approve"),
            ),
            InlineKeyboardButton(
                text="🔁 Перегенерировать",
                callback_data=make_callback(CB.HITL, hitl_id, "regen"),
            ),
        ],
    ]

    if allow_edit_prompt:
        rows.append([
            InlineKeyboardButton(
                text="✏️ Изменить промт",
                callback_data=make_callback(CB.HITL, hitl_id, "edit"),
            ),
        ])

    if allow_original:
        rows.append([
            InlineKeyboardButton(
                text="📷 Оригинал (без правки)",
                callback_data=make_callback(CB.HITL, hitl_id, "original"),
            ),
        ])

    rows.append([
        InlineKeyboardButton(
            text="❌ Отклонить",
            callback_data=make_callback(CB.HITL, hitl_id, "reject"),
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_hitl_video(hitl_id: int) -> InlineKeyboardMarkup:
    """HITL для per-frame видео (PR #33). Без 'Изменить промт' — видео-промты
    пока не редактируются вручную.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить видео",
                    callback_data=make_callback(CB.HITL, hitl_id, "approve"),
                ),
                InlineKeyboardButton(
                    text="🔁 Перегенерировать",
                    callback_data=make_callback(CB.HITL, hitl_id, "regen"),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=make_callback(CB.HITL, hitl_id, "reject"),
                ),
            ],
        ]
    )


def parse_hitl_callback(data: str) -> tuple[int, str] | None:
    """Распарсить `hitl:42:approve` → (42, 'approve'). None при невалидном.

    Используется в handler'ах для извлечения hitl_id и action из callback.
    """
    if not data or not data.startswith(CB.HITL.value + ":"):
        return None
    parts = data.split(":")
    if len(parts) < 3:
        return None
    try:
        return int(parts[1]), parts[2]
    except ValueError:
        return None


__all__ = [
    "kb_hitl_image",
    "kb_hitl_video",
    "parse_hitl_callback",
]
