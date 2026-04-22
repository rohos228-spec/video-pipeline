"""Entrypoint: поднимает Telegram-бота + загружает мастер-промты в БД + держит
фоновую петлю orchestrator-а, которая будит проект по статусу и шагает дальше."""

from __future__ import annotations

import asyncio

from loguru import logger

from app.db import engine
from app.models import Base
from app.prompts_loader import sync_prompts_from_files
from app.settings import settings
from app.telegram.bot import build_bot, dp


async def _init_db() -> None:
    """Минимальная инициализация БД (alembic пока опционально)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def main() -> None:
    logger.info("starting video-pipeline, owner chat_id={}", settings.telegram_owner_chat_id)
    await _init_db()
    await sync_prompts_from_files()

    bot, _ = await build_bot()
    logger.info("telegram bot started")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
