"""Smoke-тесты на Telegram handler AI-агента.

Проверяем что router/keyboards/filter правильно построены.
Полный e2e с Telegram-API — отдельной фазой (manual test).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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


@pytest.mark.asyncio
async def test_session_cleanup_on_send_failure() -> None:
    """Регрессия: _active_sessions очищается даже если финальный bot.send_message падает.

    Без finally-блока вокруг очистки — если edit_message_text И send_message
    оба падают, _active_sessions остаётся засорённым и owner не может
    запустить следующую сессию до перезапуска бота.
    """
    from app.ai_agent.session import RuntimeSession
    from app.models import AISessionMode
    from app.telegram.handlers import ai_agent as handler

    chat_id = 999_999_001

    # Подготовить минимальный RuntimeSession
    runtime = RuntimeSession(
        db_id=0,
        chat_id=chat_id,
        model="gpt-4o-mini",
        mode=AISessionMode.hitl_edit,
        initial_query="test",
    )
    runtime.finished = True
    runtime.final_answer = "done"

    # Зарегистрировать сессию как «активную» (симулируем cmd_ai)
    handler._active_sessions[chat_id] = runtime
    handler._active_tasks[chat_id] = asyncio.current_task()

    # Бот, у которого оба метода доставки сообщений падают
    failing_bot = MagicMock()
    failing_bot.edit_message_text = AsyncMock(side_effect=RuntimeError("tg api error"))
    failing_bot.send_message = AsyncMock(side_effect=RuntimeError("tg send also failed"))

    # run_loop мок — сессия завершается сразу без работы
    async def noop_run_loop(*a, **kw):
        return runtime

    with (
        patch("app.telegram.handlers.ai_agent.run_loop", noop_run_loop),
        patch("app.telegram.handlers.ai_agent.AIClient"),
        patch("app.telegram.handlers.ai_agent.get_config", return_value=MagicMock(is_configured=True)),
        patch("app.telegram.handlers.ai_agent.session_scope"),
    ):
        # Должно завершиться без исключения, несмотря на падение bot.send_message
        await handler._run_session_task(runtime, failing_bot, summary_msg_id=1)

    # КРИТИЧНО: очистка должна была выполниться даже при падении bot.send_message
    assert chat_id not in handler._active_sessions, (
        "_active_sessions не очищен — баг: owner не сможет запустить новую сессию"
    )
    assert chat_id not in handler._active_tasks, (
        "_active_tasks не очищен"
    )


@pytest.mark.asyncio
async def test_clarification_waits_cleared_on_session_end() -> None:
    """Регрессия: _clarification_waits очищается при завершении сессии.

    Без этого: owner нажал ✏️ (Уточнить) на HITL-карточке → сессия
    завершилась по таймауту/отмене → _clarification_waits[chat_id] остаётся.
    Следующее обычное текстовое сообщение owner'а (например, ввод темы нового
    проекта) перехватывается фильтром _is_awaiting_clarification и теряется:
    handler отвечает «Сессия уже не ждёт уточнения.» вместо того чтобы
    передать сообщение в on_text_message бота.
    """
    from app.ai_agent.session import RuntimeSession
    from app.models import AISessionMode
    from app.telegram.handlers import ai_agent as handler

    chat_id = 999_999_002

    runtime = RuntimeSession(
        db_id=0,
        chat_id=chat_id,
        model="gpt-4o-mini",
        mode=AISessionMode.hitl_edit,
        initial_query="test",
    )
    runtime.finished = True
    runtime.final_answer = "done"

    # Имитируем состояние после нажатия ✏️: сессия активна, clarification ждёт
    handler._active_sessions[chat_id] = runtime
    handler._active_tasks[chat_id] = asyncio.current_task()
    handler._clarification_waits[chat_id] = 42  # stale tool_call_db_id

    failing_bot = MagicMock()
    failing_bot.edit_message_text = AsyncMock(return_value=MagicMock())
    failing_bot.send_message = AsyncMock(return_value=MagicMock())

    async def noop_run_loop(*a, **kw):
        return runtime

    with (
        patch("app.telegram.handlers.ai_agent.run_loop", noop_run_loop),
        patch("app.telegram.handlers.ai_agent.AIClient"),
        patch("app.telegram.handlers.ai_agent.get_config", return_value=MagicMock(is_configured=True)),
        patch("app.telegram.handlers.ai_agent.session_scope"),
    ):
        await handler._run_session_task(runtime, failing_bot, summary_msg_id=1)

    # КРИТИЧНО: stale запись должна быть убрана, иначе следующий обычный
    # текст owner'а будет перехвачен и потерян.
    assert chat_id not in handler._clarification_waits, (
        "_clarification_waits не очищен — следующее текстовое сообщение owner'а "
        "будет молча перехвачено и потеряно фильтром _is_awaiting_clarification"
    )
