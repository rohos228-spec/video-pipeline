"""Фоновый воркер: периодически сканирует БД и продвигает проекты по стейтам.

Каждый тик:
  1. выбираем проекты, которые не в терминальных статусах,
  2. для каждого вызываем advance_project (который решит, делать шаг или ждать
     решения по HITL).
"""

from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import select

from app.db import engine, session_scope
from app.models import Base, Project, ProjectStatus
from app.orchestrator.pipeline import advance_project
from app.prompts_loader import sync_prompts_from_files
from app.settings import settings

ACTIVE_STATUSES = [
    ProjectStatus.planning,
    ProjectStatus.plan_ready,
    ProjectStatus.script_ready,
    ProjectStatus.frames_ready,
    ProjectStatus.hero_ready,
    ProjectStatus.images_ready,
    ProjectStatus.animation_prompts_ready,
    ProjectStatus.videos_ready,
    ProjectStatus.audio_ready,
    ProjectStatus.assembled,
]


async def _loop_once(bot) -> None:  # noqa: ANN001 — aiogram.Bot | NoopBot
    async with session_scope() as s:
        projects = (
            await s.execute(select(Project).where(Project.status.in_(ACTIVE_STATUSES)))
        ).scalars().all()
        for p in projects:
            try:
                await advance_project(s, p, bot)
            except Exception as e:  # noqa: BLE001
                logger.exception("advance_project failed for #{}", p.id)
                # оповещаем владельца в Telegram, чтобы он видел, что бот
                # не висит молча
                try:
                    msg = f"⚠️ Ошибка на проекте #{p.id} (статус={p.status.value}): {type(e).__name__}: {e}"
                    await bot.send_message(settings.telegram_owner_chat_id, msg[:3800])
                except Exception:  # noqa: BLE001
                    logger.warning("не удалось отправить уведомление об ошибке в Telegram")


async def main() -> None:
    logger.info("worker starting, owner chat_id={}", settings.telegram_owner_chat_id)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await sync_prompts_from_files()

    from app.telegram.noop_bot import get_worker_bot

    bot = get_worker_bot(None)
    if settings.telegram_active:
        from aiogram import Bot

        bot = Bot(settings.telegram_bot_token)
    try:
        while True:
            try:
                await _loop_once(bot)
            except Exception:  # noqa: BLE001
                logger.exception("worker loop iteration failed")
            await asyncio.sleep(5)
    finally:
        if hasattr(bot, "session"):
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
