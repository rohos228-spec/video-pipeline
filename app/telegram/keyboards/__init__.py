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
from app.telegram.keyboards.hitl_buttons import (
    kb_hitl_image,
    kb_hitl_video,
    parse_hitl_callback,
)
from app.telegram.keyboards.main_menu import (
    kb_main_menu,
    kb_mass_pause_resume,
)
from app.telegram.keyboards.project_menu import (
    kb_project_delete_confirm,
    kb_project_menu,
    kb_reset_step_confirm,
)

__all__ = [
    # common
    "kb_back_to_main",
    "kb_hitl_4buttons",
    "kb_session_summary",
    "kb_yes_no",
    "make_callback",
    "row_back_menu",
    # main_menu
    "kb_main_menu",
    "kb_mass_pause_resume",
    # project_menu
    "kb_project_delete_confirm",
    "kb_project_menu",
    "kb_reset_step_confirm",
    # hitl_buttons
    "kb_hitl_image",
    "kb_hitl_video",
    "parse_hitl_callback",
]
