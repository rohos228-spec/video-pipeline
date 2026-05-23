"""Smoke-тесты на Telegram handler AI-агента.

Проверяем что router/keyboards/filter правильно построены.
Полный e2e с Telegram-API — отдельной фазой (manual test).
"""

from __future__ import annotations


def test_ai_router_importable() -> None:
    """Handler модуль импортируется без падений."""
    from app.telegram.handlers import ai_agent

    assert ai_agent.router is not None
    assert ai_agent.router.name == "ai_agent"


def test_handlers_registered() -> None:
    """В router'е зарегистрированы все обработчики команд + callback'ов."""
    from app.telegram.handlers.ai_agent import router

    # message handlers
    msg_handlers = router.message.handlers
    assert len(msg_handlers) >= 2  # /ai команда + text clarification

    # callback handlers
    cb_handlers = router.callback_query.handlers
    assert len(cb_handlers) >= 6  # approve/reject/regen/clarify/cancel/status/noop


def test_kb_factories_produce_valid_markup() -> None:
    """Все клавиатуры строятся и проходят aiogram-сериализацию."""
    from app.telegram.handlers.ai_agent import _hitl_kb, _summary_kb

    kb = _summary_kb(42)
    assert kb.inline_keyboard
    # callback_data на каждой кнопке валидные (< 64 байт)
    for row in kb.inline_keyboard:
        for btn in row:
            assert btn.callback_data is None or len(btn.callback_data.encode()) <= 64

    kb = _hitl_kb(123)
    assert len(kb.inline_keyboard) == 2  # 2 ряда по 2 кнопки
    for row in kb.inline_keyboard:
        for btn in row:
            assert btn.callback_data is None or len(btn.callback_data.encode()) <= 64


def test_format_args_preview_edit_file() -> None:
    from app.telegram.handlers.ai_agent import _format_args_preview

    preview = _format_args_preview(
        "edit_file",
        {
            "path": "app/telegram/bot.py",
            "old_string": "x" * 50,
            "new_string": "y" * 50,
        },
    )
    assert "app/telegram/bot.py" in preview
    assert "<b>− Было:</b>" in preview
    assert "<b>+ Стало:</b>" in preview


def test_format_args_preview_truncates_long_content() -> None:
    from app.telegram.handlers.ai_agent import _format_args_preview

    huge = "a" * 5000
    preview = _format_args_preview(
        "edit_file", {"path": "x.py", "old_string": huge, "new_string": "y"}
    )
    assert "truncated" in preview
    # Total preview не должен быть гигантским
    assert len(preview) < 4000


def test_format_args_preview_html_escape() -> None:
    """HTML-теги в args должны быть экранированы (PR #32 регрессия)."""
    from app.telegram.handlers.ai_agent import _format_args_preview

    preview = _format_args_preview(
        "edit_file",
        {"path": "<script>", "old_string": "<b>x</b>", "new_string": "</b>"},
    )
    assert "<script>" not in preview  # должен быть escaped
    assert "&lt;script&gt;" in preview


def test_callback_data_prefixes() -> None:
    """Префиксы callback_data соответствуют ожиданиям (для аудита кнопок)."""
    from app.telegram.handlers.ai_agent import _hitl_kb, _summary_kb

    hitl_kb = _hitl_kb(99)
    callbacks = [
        btn.callback_data for row in hitl_kb.inline_keyboard for btn in row
    ]
    # все начинаются с ai:
    assert all(c.startswith("ai:") for c in callbacks)
    # имеют tool_call_id
    assert any(":99" in c for c in callbacks)

    summary_kb = _summary_kb(42)
    summary_callbacks = [
        btn.callback_data for row in summary_kb.inline_keyboard for btn in row
    ]
    assert any("ai:cancel:42" in c for c in summary_callbacks)


def test_dp_integration() -> None:
    """В Dispatcher из bot.py роутер ai_agent действительно подключён."""
    import app.telegram.bot

    # routers подключаются в dp.sub_routers
    routers = app.telegram.bot.dp.sub_routers
    router_names = {r.name for r in routers}
    assert "ai_agent" in router_names
