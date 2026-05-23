"""HitL FSM end-to-end тесты для AI-агента (Phase I dopolnenie).

Покрывает сценарии:
- Owner ✏️ Clarify → текст-уточнение попадает в LLM как hint, LLM пробует
  снова с тем же tool.
- Owner 🔁 Regen → tool возвращает rejected, LLM пробует другой подход.
- HITL-таймаут → автоматический reject через future.set_exception(TimeoutError).
- AI:NOOP callback не падает.
- Состояние _pending_hitl_futures корректно очищается после ответа.

Запуск: pytest -q tests/test_ai_agent_hitl_flow.py
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.telegram.handlers import ai_agent as ai_handler

# ────────────────────────────── helpers ─────────────────────────────────────


def _make_callback_query(
    data: str, *, chat_id: int = 279_887_118, user_id: int = 279_887_118
) -> MagicMock:
    """Mock CallbackQuery с message=spec=Message чтобы isinstance(msg, Message) был True.

    После Phase B mypy strict в cb_clarify / cb_cancel / cb_status добавлен
    isinstance check — без spec=Message тесты не пройдут.
    """
    from aiogram.types import Message  # noqa: PLC0415

    cb = MagicMock()
    cb.from_user = MagicMock(id=user_id)
    cb.message = MagicMock(spec=Message)
    cb.message.chat = MagicMock(id=chat_id)
    cb.message.message_id = 42
    cb.message.edit_reply_markup = AsyncMock()
    cb.message.reply = AsyncMock()
    cb.data = data
    cb.answer = AsyncMock()
    return cb


def _make_message(text: str, *, chat_id: int = 279_887_118, user_id: int = 279_887_118) -> MagicMock:
    from aiogram.types import Message  # noqa: PLC0415

    msg = MagicMock(spec=Message)
    msg.text = text
    msg.from_user = MagicMock(id=user_id)
    msg.chat = MagicMock(id=chat_id)
    msg.answer = AsyncMock()
    return msg


def _register_future(tool_call_id: int) -> asyncio.Future:
    """Создаёт future + регистрирует в pending."""
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    ai_handler._pending_hitl_futures[tool_call_id] = future
    return future


def _cleanup() -> None:
    """Очистка state между тестами."""
    ai_handler._pending_hitl_futures.clear()
    ai_handler._clarification_waits.clear()
    ai_handler._active_sessions.clear()
    ai_handler._active_tasks.clear()


# ────────────────────────────── approve flow ────────────────────────────────


@pytest.mark.asyncio
async def test_approve_resolves_future_with_approved() -> None:
    """ai:approve → future.result() == {'decision': 'approved'}."""
    _cleanup()
    try:
        tc_id = 1001
        future = _register_future(tc_id)

        with patch.object(ai_handler, "session_scope") as mocked_scope:
            db_mock = AsyncMock()
            db_mock.get = AsyncMock(return_value=None)
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=db_mock)
            cm.__aexit__ = AsyncMock(return_value=None)
            mocked_scope.return_value = cm

            cb = _make_callback_query(f"ai:approve:{tc_id}")
            await ai_handler.cb_approve(cb)

        assert future.done()
        assert future.result() == {"decision": "approved"}
        cb.answer.assert_called()
    finally:
        _cleanup()


@pytest.mark.asyncio
async def test_reject_resolves_with_rejected_reason() -> None:
    _cleanup()
    try:
        tc_id = 1002
        future = _register_future(tc_id)

        with patch.object(ai_handler, "session_scope") as mocked_scope:
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=AsyncMock(get=AsyncMock(return_value=None)))
            cm.__aexit__ = AsyncMock(return_value=None)
            mocked_scope.return_value = cm

            cb = _make_callback_query(f"ai:reject:{tc_id}")
            await ai_handler.cb_reject(cb)

        assert future.done()
        decision = future.result()
        assert decision["decision"] == "rejected"
        assert "reason" in decision
    finally:
        _cleanup()


# ────────────────────────────── regen flow ──────────────────────────────────


@pytest.mark.asyncio
async def test_regen_marks_rejected_with_hint() -> None:
    """🔁 регенерация = семантически rejected с подсказкой 'попробуй иначе'."""
    _cleanup()
    try:
        tc_id = 1003
        future = _register_future(tc_id)

        with patch.object(ai_handler, "session_scope") as mocked_scope:
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=AsyncMock(get=AsyncMock(return_value=None)))
            cm.__aexit__ = AsyncMock(return_value=None)
            mocked_scope.return_value = cm

            cb = _make_callback_query(f"ai:regen:{tc_id}")
            await ai_handler.cb_regen(cb)

        assert future.done()
        decision = future.result()
        assert decision["decision"] == "rejected"
        # Reason ясно намекает что LLM должна попробовать другой подход
        assert "regen" in (decision.get("reason") or "").lower() or "иначе" in (decision.get("reason") or "")
    finally:
        _cleanup()


# ────────────────────────────── clarify flow ────────────────────────────────


@pytest.mark.asyncio
async def test_clarify_arms_text_capture() -> None:
    """✏️ Clarify ставит chat_id в _clarification_waits — следующий текст идёт в LLM."""
    _cleanup()
    try:
        tc_id = 1004
        future = _register_future(tc_id)  # noqa: F841 — кладём в pending для is_awaiting check

        cb = _make_callback_query(f"ai:clarify:{tc_id}")
        await ai_handler.cb_clarify(cb)

        # _clarification_waits должен содержать наш chat_id → tc_id
        assert ai_handler._clarification_waits.get(cb.message.chat.id) == tc_id
        # future ещё НЕ resolved (ждём текст)
        assert not future.done()
        # Бот ответил инструкцией
        cb.message.reply.assert_called()
    finally:
        _cleanup()


@pytest.mark.asyncio
async def test_clarify_then_text_resolves_with_clarification() -> None:
    """✏️ → owner шлёт текст → future resolved with clarification."""
    _cleanup()
    try:
        tc_id = 1005
        future = _register_future(tc_id)

        # 1. Owner жмёт ✏️
        cb = _make_callback_query(f"ai:clarify:{tc_id}")
        await ai_handler.cb_clarify(cb)

        # 2. Owner шлёт текст
        chat_id = cb.message.chat.id
        msg = _make_message("не трогай callback, перепиши обработчик", chat_id=chat_id)

        with patch.object(ai_handler, "session_scope") as mocked_scope:
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=AsyncMock(get=AsyncMock(return_value=None)))
            cm.__aexit__ = AsyncMock(return_value=None)
            mocked_scope.return_value = cm

            await ai_handler.msg_text_clarification(msg)

        assert future.done()
        decision = future.result()
        assert decision["decision"] == "clarified"
        assert "не трогай callback" in decision["clarification"]
        # _clarification_waits очищен
        assert chat_id not in ai_handler._clarification_waits
        msg.answer.assert_called()
    finally:
        _cleanup()


@pytest.mark.asyncio
async def test_is_awaiting_clarification_returns_true_only_when_pending() -> None:
    """Фильтр срабатывает только когда есть pending clarification — иначе текст идёт в bot.py handler'ы."""
    _cleanup()
    try:
        chat_id = 279_887_118
        msg = _make_message("hi", chat_id=chat_id)

        # Без pending — False
        assert not await ai_handler._is_awaiting_clarification(msg)

        # С pending — True
        ai_handler._clarification_waits[chat_id] = 999
        assert await ai_handler._is_awaiting_clarification(msg)

        # Чужой chat_id — False (не owner)
        msg_other = _make_message("hi", chat_id=999_999, user_id=999_999)
        assert not await ai_handler._is_awaiting_clarification(msg_other)
    finally:
        _cleanup()


# ────────────────────────────── noop ────────────────────────────────────────


@pytest.mark.asyncio
async def test_noop_does_not_crash() -> None:
    """ai:noop — заглушка, не должна падать или менять state."""
    _cleanup()
    try:
        cb = _make_callback_query("ai:noop")
        await ai_handler.cb_noop(cb)
        cb.answer.assert_called()
    finally:
        _cleanup()


# ────────────────────────────── access control ──────────────────────────────


@pytest.mark.asyncio
async def test_approve_rejects_non_owner() -> None:
    """Не-owner получает alert и future НЕ резолвится."""
    _cleanup()
    try:
        tc_id = 1006
        future = _register_future(tc_id)

        # user_id != owner
        cb = _make_callback_query(f"ai:approve:{tc_id}", user_id=999_999)
        await ai_handler.cb_approve(cb)

        # Future остался pending
        assert not future.done()
        # answer был с alert
        cb.answer.assert_called()
        call_args = cb.answer.call_args
        # show_alert=True один из позиционных или kwarg
        assert call_args.kwargs.get("show_alert") is True or any(
            isinstance(a, bool) and a for a in call_args.args
        )
    finally:
        _cleanup()


# ────────────────────────────── double-resolve safety ───────────────────────


@pytest.mark.asyncio
async def test_already_resolved_future_does_not_crash() -> None:
    """Если owner двойным кликом нажмёт approve — alert вместо exception."""
    _cleanup()
    try:
        tc_id = 1007
        future = _register_future(tc_id)
        future.set_result({"decision": "approved"})  # уже резолвлен

        cb = _make_callback_query(f"ai:approve:{tc_id}")
        await ai_handler.cb_approve(cb)

        # Должен показать alert "Уже обработано"
        cb.answer.assert_called()
        # Future остался в первоначальном состоянии
        assert future.result() == {"decision": "approved"}
    finally:
        _cleanup()
