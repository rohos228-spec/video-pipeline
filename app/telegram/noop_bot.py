"""Заглушка Bot для режима без Telegram.

Воркер и шаги пайплайна типизированы под aiogram.Bot. В web-only режиме
передаём NoopBot — все send_* становятся no-op, HITL идёт через веб-UI.
"""

from __future__ import annotations

from typing import Any, cast

from aiogram import Bot


class _FakeMessage:
    message_id = 0
    chat = None


class NoopBot:
    """Минимальная совместимость с вызовами bot.send_* в шагах."""

    async def send_message(self, *args: Any, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage()

    async def send_photo(self, *args: Any, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage()

    async def send_document(self, *args: Any, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage()

    async def send_video(self, *args: Any, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage()

    async def edit_message_reply_markup(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def edit_message_caption(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def edit_message_text(self, *args: Any, **kwargs: Any) -> None:
        return None

    class session:
        @staticmethod
        async def close() -> None:
            return None


_noop_singleton = NoopBot()


def get_worker_bot(real_bot: Bot | None) -> Bot:
    """Bot для воркера: реальный или no-op."""
    if real_bot is not None:
        return real_bot
    return cast(Bot, _noop_singleton)
