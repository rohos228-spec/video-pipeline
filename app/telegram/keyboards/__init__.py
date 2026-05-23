"""Фабрики inline-клавиатур (Phase E.4 step 2 foundation).

Цель — централизованные, типизированные фабрики которые:
- используют константы из `app.telegram.callback_registry.CB` (никаких строковых литералов);
- гарантируют 64-байтный лимит callback_data на этапе сборки;
- содержат стандартные элементы: «Назад», «В меню», 4-кнопочная HITL и т.п.

Сейчас (фундамент): используется в новых handlers'ах (`ai_agent.py`,
`debug.py`). Постепенная миграция bot.py — отдельными мини-PR'ами серии E.4.

См. AGENTS.md §10 и PLAN.md §6 (Phase E).
"""

from app.telegram.keyboards.common import (
    kb_back_to_main,
    kb_hitl_4buttons,
    kb_session_summary,
    kb_yes_no,
    make_callback,
    row_back_menu,
)

__all__ = [
    "kb_back_to_main",
    "kb_hitl_4buttons",
    "kb_session_summary",
    "kb_yes_no",
    "make_callback",
    "row_back_menu",
]
