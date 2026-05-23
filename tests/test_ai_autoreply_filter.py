"""Тесты на _should_autoreply filter — главный invariant."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from app.telegram.handlers.ai_agent import _should_autoreply


def _make_msg(
    text: str = "привет",
    *,
    user_id: int = 279_887_118,  # owner
    chat_id: int = 279_887_118,
    chat_type: str = "private",
    is_command: bool = False,
    reply_to: object | None = None,
) -> MagicMock:
    from aiogram.types import Message

    msg = MagicMock(spec=Message)
    msg.from_user = MagicMock(id=user_id)
    msg.chat = MagicMock(id=chat_id, type=chat_type)
    msg.text = ("/" + text) if is_command else text
    msg.reply_to_message = reply_to
    return msg


@pytest.fixture(autouse=True)
def _reset_state():
    """Чистим глобальные state между тестами."""
    from app.telegram.handlers.ai_agent import (
        _active_sessions,
        _active_tasks,
        _clarification_waits,
    )

    _active_sessions.clear()
    _active_tasks.clear()
    _clarification_waits.clear()
    yield
    _active_sessions.clear()
    _active_tasks.clear()
    _clarification_waits.clear()


# ──────────────────────────── disabled by default ───────────────────────────


@pytest.mark.asyncio
async def test_disabled_by_default() -> None:
    """Без AI_AGENT_AUTOREPLY=true filter возвращает False."""
    with patch.dict(os.environ, {"AI_AGENT_AUTOREPLY": ""}, clear=False):
        msg = _make_msg("привет")
        assert await _should_autoreply(msg) is False


# ──────────────────────────── enabled — позитивные case'ы ───────────────────


@pytest.mark.asyncio
async def test_enabled_owner_private_chat_allowed() -> None:
    """Включено + owner + личка + нет pending → True."""
    with patch.dict(os.environ, {"AI_AGENT_AUTOREPLY": "true"}, clear=False):
        with patch("app.telegram.bot.has_pending_input", return_value=False):
            msg = _make_msg("привет, что нового?")
            assert await _should_autoreply(msg) is True


@pytest.mark.asyncio
async def test_enabled_truthy_variants() -> None:
    """1, true, yes, on — всё работает."""
    for val in ("1", "true", "TRUE", "yes", "on"):
        with patch.dict(os.environ, {"AI_AGENT_AUTOREPLY": val}, clear=False):
            with patch("app.telegram.bot.has_pending_input", return_value=False):
                msg = _make_msg("hi")
                assert await _should_autoreply(msg) is True, f"val={val}"


# ──────────────────────────── negative cases ────────────────────────────────


@pytest.mark.asyncio
async def test_not_owner_rejected() -> None:
    with patch.dict(os.environ, {"AI_AGENT_AUTOREPLY": "true"}, clear=False):
        msg = _make_msg("hi", user_id=99_999_999)
        assert await _should_autoreply(msg) is False


@pytest.mark.asyncio
async def test_group_chat_rejected() -> None:
    """В групповом чате — никогда (только personal)."""
    with patch.dict(os.environ, {"AI_AGENT_AUTOREPLY": "true"}, clear=False):
        msg = _make_msg("hi", chat_type="supergroup")
        assert await _should_autoreply(msg) is False


@pytest.mark.asyncio
async def test_empty_text_rejected() -> None:
    with patch.dict(os.environ, {"AI_AGENT_AUTOREPLY": "true"}, clear=False):
        msg = _make_msg("")
        assert await _should_autoreply(msg) is False


@pytest.mark.asyncio
async def test_command_rejected() -> None:
    """Команды (/start, /menu, и т.д.) не перехватываются."""
    with patch.dict(os.environ, {"AI_AGENT_AUTOREPLY": "true"}, clear=False):
        msg = _make_msg("menu", is_command=True)  # /menu
        assert await _should_autoreply(msg) is False


@pytest.mark.asyncio
async def test_persistent_button_text_rejected() -> None:
    """Тексты persistent-keyboard кнопок не перехватываются."""
    with patch.dict(os.environ, {"AI_AGENT_AUTOREPLY": "true"}, clear=False):
        for btn_text in ("🏠 Главное меню", "📁 Последний проект", "↩ Назад"):
            msg = _make_msg(btn_text)
            assert await _should_autoreply(msg) is False, btn_text


@pytest.mark.asyncio
async def test_has_pending_input_rejected() -> None:
    """Если bot.py ждёт от owner'а текст (тема, имя промта, ...) — False."""
    with patch.dict(os.environ, {"AI_AGENT_AUTOREPLY": "true"}, clear=False):
        with patch("app.telegram.bot.has_pending_input", return_value=True):
            msg = _make_msg("моя новая тема для ролика")
            assert await _should_autoreply(msg) is False


@pytest.mark.asyncio
async def test_active_ai_session_rejected() -> None:
    """Если уже идёт AI-сессия — новый текст не запускает вторую."""
    from app.telegram.handlers.ai_agent import _active_sessions

    chat_id = 279_887_118
    _active_sessions[chat_id] = MagicMock()
    try:
        with patch.dict(os.environ, {"AI_AGENT_AUTOREPLY": "true"}, clear=False):
            with patch("app.telegram.bot.has_pending_input", return_value=False):
                msg = _make_msg("hi", chat_id=chat_id)
                assert await _should_autoreply(msg) is False
    finally:
        _active_sessions.pop(chat_id, None)


@pytest.mark.asyncio
async def test_reply_to_message_rejected() -> None:
    """reply_to_message — отдельный case (например HITL картинки)."""
    with patch.dict(os.environ, {"AI_AGENT_AUTOREPLY": "true"}, clear=False):
        with patch("app.telegram.bot.has_pending_input", return_value=False):
            reply_to = MagicMock()
            msg = _make_msg("hi", reply_to=reply_to)
            assert await _should_autoreply(msg) is False


# ──────────────────────────── has_pending_input integration ─────────────────


def test_has_pending_input_returns_false_for_empty() -> None:
    """bot.py:has_pending_input для пустого state — False."""
    from app.telegram.bot import has_pending_input

    assert has_pending_input(999_999_999) is False


def test_has_pending_input_picks_up_pending() -> None:
    """Если в bot.py есть pending dict с user_id — has_pending_input=True."""
    from app.telegram.bot import _pending_topic_input, has_pending_input

    test_user = 555_555_555
    _pending_topic_input[test_user] = True
    try:
        assert has_pending_input(test_user) is True
    finally:
        _pending_topic_input.pop(test_user, None)


def test_has_pending_input_covers_all_pending_dicts() -> None:
    """Проверка что has_pending_input реально проверяет все 28 dict'ов."""
    from app.telegram import bot as bot_module
    from app.telegram.bot import has_pending_input

    # Composite-key dicts (key=tuple) — это caches/content, не user state.
    # has_pending_input их игнорирует (они не индексируются по user_id).
    _COMPOSITE_KEY_DICTS = {
        "_pending_mass_prompt_content",
        "_pending_mass_text_content",
    }
    pending_dicts = {
        name: getattr(bot_module, name)
        for name in dir(bot_module)
        if (
            name.startswith("_pending_")
            and isinstance(getattr(bot_module, name), dict)
            and name not in _COMPOSITE_KEY_DICTS
        )
    }

    # Для каждого — добавляем user, проверяем has_pending=True, удаляем
    test_user = 444_444_444
    for name, d in pending_dicts.items():
        # Значение игнорируется, главное user_id key.
        # Некоторые dict'ы используют tuple key — пропустим их.
        try:
            d[test_user] = True
        except TypeError:
            continue
        try:
            assert has_pending_input(test_user) is True, (
                f"has_pending_input не видит _{name}!"
            )
        finally:
            d.pop(test_user, None)
