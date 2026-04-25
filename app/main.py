"""Entrypoint: поднимает Telegram-бота + фоновый воркер (оркестратор) в одном
процессе. На Windows без Docker это самый простой способ — один терминал, один
процесс, Ctrl+C для остановки.

Запуск:
    python -m app.main

Что делает:
  1. Создаёт таблицы БД (SQLite), если их ещё нет.
  2. Синкует мастер-промты из `prompts/*.vN.md` в БД.
  3. Запускает aiogram-поллинг (TG-бот: /new, /status, HITL-кнопки).
  4. Параллельно — фоновый воркер, который продвигает проекты по статусам.
"""

from __future__ import annotations

import asyncio
import contextlib

from loguru import logger

from app.db import engine
from app.models import Base
from app.prompts_loader import sync_prompts_from_files
from app.settings import settings
from app.telegram.bot import build_bot, dp


async def _init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _run_worker_loop(bot) -> None:
    """Фоновая петля воркера: сканирует БД и продвигает проекты.

    Анти-зацикливание: если один и тот же шаг падает >= MAX_FAIL раз подряд,
    ставим проект в статус `failed` и шлём в TG финальное уведомление.
    До этого шлём только первое сообщение на каждый новый шаг (чтобы не
    спамить одинаковыми ошибками).
    """
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Project, ProjectStatus
    from app.orchestrator.pipeline import advance_project

    MAX_FAIL = 3
    # (project_id, status.value) -> кол-во подряд неудач на этом шаге
    fail_counts: dict[tuple[int, str], int] = {}

    active = [
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
    while True:
        try:
            async with session_scope() as s:
                projects = (
                    await s.execute(select(Project).where(Project.status.in_(active)))
                ).scalars().all()
                for p in projects:
                    key = (p.id, p.status.value)
                    try:
                        await advance_project(s, p, bot)
                        # успех на этом шаге — сбрасываем счётчик
                        fail_counts.pop(key, None)
                    except Exception as e:  # noqa: BLE001
                        logger.exception("advance_project failed for #{}", p.id)
                        prev = fail_counts.get(key, 0)
                        fail_counts[key] = prev + 1
                        try:
                            if prev == 0:
                                # Первая ошибка на этом шаге — сообщаем.
                                msg = (
                                    f"⚠️ Ошибка на проекте #{p.id} "
                                    f"(статус={p.status.value}): "
                                    f"{type(e).__name__}: {e}"
                                )
                                await bot.send_message(
                                    settings.telegram_owner_chat_id, msg[:3800]
                                )
                            elif fail_counts[key] >= MAX_FAIL:
                                # Проект зависает на одном шаге — паркуем.
                                p.status = ProjectStatus.failed
                                await s.flush()
                                await bot.send_message(
                                    settings.telegram_owner_chat_id,
                                    (
                                        f"🛑 Проект #{p.id} переведён в failed "
                                        f"после {MAX_FAIL} ошибок подряд на шаге "
                                        f"{key[1]}. Последняя ошибка: "
                                        f"{type(e).__name__}: {e}"
                                    )[:3800],
                                )
                        except Exception:  # noqa: BLE001
                            logger.warning(
                                "не удалось отправить уведомление об ошибке в Telegram"
                            )
        except Exception:  # noqa: BLE001
            logger.exception("worker loop iteration failed")
        await asyncio.sleep(15)


async def main() -> None:
    logger.info(
        "starting video-pipeline, owner chat_id={}, db={}",
        settings.telegram_owner_chat_id,
        settings.db_url,
    )
    await _init_db()
    await sync_prompts_from_files()

    bot, _ = await build_bot()
    logger.info("telegram bot + worker started")
    # Крутим поллинг и воркер параллельно. Если один упадёт — оба завершаются.
    polling_task = asyncio.create_task(
        dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    )
    worker_task = asyncio.create_task(_run_worker_loop(bot))
    try:
        # FIRST_COMPLETED, а не FIRST_EXCEPTION: воркер-петля ловит все исключения
        # внутри себя и никогда «не падает», так что FIRST_EXCEPTION ждал бы
        # вечно, если поллинг завершится штатно (Ctrl+C, graceful disconnect).
        done, pending = await asyncio.wait(
            [polling_task, worker_task], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        for t in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        for t in done:
            exc = t.exception()
            if exc is not None:
                raise exc
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
