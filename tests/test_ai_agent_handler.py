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


# ────────────────────────────────────────────────────────────────────────────
# Регрессионный тест: cleanup _active_sessions ВСЕГДА должен происходить
# (Phase H применение фикса из параллельного PR #40).
# ────────────────────────────────────────────────────────────────────────────


def test_session_cleanup_on_send_failure() -> None:
    """Если bot.edit_message_text И bot.send_message упадут, _active_sessions
    и _active_tasks всё равно должны быть очищены — иначе owner залочен.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.ai_agent.session import RuntimeSession
    from app.models import AISessionMode
    from app.telegram.handlers.ai_agent import (
        _active_sessions,
        _active_tasks,
        _run_session_task,
    )

    chat_id = 999_999_999
    runtime = RuntimeSession(
        db_id=99999,
        chat_id=chat_id,
        model="gpt-4o-mini",
        mode=AISessionMode.qa,
        initial_query="test",
    )
    runtime.finished = True
    runtime.final_answer = "done"

    # Помещаем в активные сессии — это то что должно очиститься
    _active_sessions[chat_id] = runtime
    _active_tasks[chat_id] = MagicMock()  # placeholder, не реальный task
    try:
        # Mock bot — оба метода падают
        bot = MagicMock()
        bot.edit_message_text = AsyncMock(side_effect=RuntimeError("edit failed"))
        bot.send_message = AsyncMock(side_effect=RuntimeError("send failed"))

        # Mock run_loop — сразу возвращает session как finished
        async def fake_run_loop(*args, **kwargs):
            return runtime

        with patch("app.telegram.handlers.ai_agent.run_loop", side_effect=fake_run_loop):
            with patch("app.telegram.handlers.ai_agent.AIClient", MagicMock()):
                # close_session тоже мокаем чтобы DB-операции не падали
                with patch("app.telegram.handlers.ai_agent.session_scope") as mocked_scope:
                    async_cm = AsyncMock()
                    async_cm.__aenter__ = AsyncMock(return_value=AsyncMock())
                    async_cm.__aexit__ = AsyncMock(return_value=None)
                    mocked_scope.return_value = async_cm

                    asyncio.run(_run_session_task(runtime, bot, summary_msg_id=42))

        # ❗ КРИТИЧНО: сессия должна быть очищена несмотря на оба сбоя
        assert chat_id not in _active_sessions, (
            "_active_sessions[chat_id] не очищен — owner залочен!"
        )
        assert chat_id not in _active_tasks, (
            "_active_tasks[chat_id] не очищен!"
        )
    finally:
        # На всякий случай чистим, если тест провалится
        _active_sessions.pop(chat_id, None)
        _active_tasks.pop(chat_id, None)


def test_clarification_waits_cleared_on_session_end() -> None:
    """PR #41: после завершения сессии _clarification_waits[chat_id] очищается.

    Иначе если owner нажал ✏️ Clarify но не прислал текст, и сессия упала по
    таймауту — stale-entry останется и съест любой следующий текст owner'а.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.ai_agent.session import RuntimeSession
    from app.models import AISessionMode
    from app.telegram.handlers.ai_agent import (
        _active_sessions,
        _active_tasks,
        _clarification_waits,
        _run_session_task,
    )

    chat_id = 999_999_111
    runtime = RuntimeSession(
        db_id=88_888,
        chat_id=chat_id,
        model="gpt-4o-mini",
        mode=AISessionMode.qa,
        initial_query="test",
    )
    runtime.finished = True
    runtime.final_answer = "done"

    _active_sessions[chat_id] = runtime
    _active_tasks[chat_id] = MagicMock()  # placeholder, не реальный task
    # Эмулируем что owner нажал ✏️ — _clarification_waits заполнен
    _clarification_waits[chat_id] = 12345

    try:
        bot = MagicMock()
        bot.edit_message_text = AsyncMock()
        bot.send_message = AsyncMock()

        async def fake_run_loop(*args, **kwargs):
            return runtime

        with patch("app.telegram.handlers.ai_agent.run_loop", side_effect=fake_run_loop):
            with patch("app.telegram.handlers.ai_agent.AIClient", MagicMock()):
                with patch("app.telegram.handlers.ai_agent.session_scope") as mocked_scope:
                    cm = AsyncMock()
                    cm.__aenter__ = AsyncMock(return_value=AsyncMock())
                    cm.__aexit__ = AsyncMock(return_value=None)
                    mocked_scope.return_value = cm

                    asyncio.run(_run_session_task(runtime, bot, summary_msg_id=100))

        # Главная проверка PR #41: _clarification_waits ОЧИЩЕН
        assert chat_id not in _clarification_waits, (
            "_clarification_waits[chat_id] не очищен — будущий текст owner'а съестся!"
        )
        # И остальное тоже почищено
        assert chat_id not in _active_sessions
        assert chat_id not in _active_tasks
    finally:
        _active_sessions.pop(chat_id, None)
        _active_tasks.pop(chat_id, None)
        _clarification_waits.pop(chat_id, None)
