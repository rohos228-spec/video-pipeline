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
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Лёгкая миграция: добавляем новые колонки в projects, если их ещё
        # нет (create_all не умеет ALTER). SQLite поддерживает IF NOT EXISTS
        # через PRAGMA table_info, но проще — try/except на ADD COLUMN.
        _new_cols = [
            ("image_generator", "VARCHAR(40)"),
            ("aspect_ratio", "VARCHAR(10)"),
            ("image_resolution", "VARCHAR(10)"),
            ("image_relax", "BOOLEAN DEFAULT 0"),
            ("video_generator", "VARCHAR(40)"),
            ("video_resolution", "VARCHAR(10)"),
            ("video_relax", "BOOLEAN DEFAULT 0"),
            ("hero_count", "INTEGER"),
            ("hero_descriptions", "JSON"),
            ("hero_variations", "JSON"),
            ("prompt_overrides", "JSON"),
        ]
        cols_rows = (
            await conn.exec_driver_sql("PRAGMA table_info(projects)")
        ).fetchall()
        existing = {row[1] for row in cols_rows}
        for col, ctype in _new_cols:
            if col in existing:
                continue
            try:
                await conn.exec_driver_sql(
                    f"ALTER TABLE projects ADD COLUMN {col} {ctype}"
                )
                logger.info("migrate: projects.{} added", col)
            except Exception as e:  # noqa: BLE001
                logger.warning("migrate: add column {} failed: {}", col, e)
        _ = text  # keep import usage neutral


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

    # Воркер запускает только «running»-статусы. «ready»-статусы — это
    # ожидание действия пользователя из TG-меню, авто-advance отключён.
    active = [
        ProjectStatus.planning,
        ProjectStatus.scripting,
        ProjectStatus.splitting,
        ProjectStatus.generating_hero,
        ProjectStatus.generating_image_prompts,
        ProjectStatus.generating_images,
        ProjectStatus.generating_animation_prompts,
        ProjectStatus.generating_videos,
        ProjectStatus.generating_audio,
        ProjectStatus.assembling,
        ProjectStatus.publishing,
    ]
    from app.telegram.bot import notify_step_done

    while True:
        try:
            async with session_scope() as s:
                projects = (
                    await s.execute(select(Project).where(Project.status.in_(active)))
                ).scalars().all()
                for p in projects:
                    key = (p.id, p.status.value)
                    prev_status_value = p.status.value
                    try:
                        await advance_project(s, p, bot)
                        # успех на этом шаге — сбрасываем счётчик
                        fail_counts.pop(key, None)
                        # если статус изменился — коммитим прямо сейчас
                        # и шлём уведомление в TG (notify_step_done читает из
                        # отдельной сессии, поэтому commit обязателен).
                        if p.status.value != prev_status_value:
                            new_status = p.status.value
                            project_id = p.id
                            await s.commit()
                            try:
                                await notify_step_done(
                                    bot, project_id, prev_status_value, new_status
                                )
                            except Exception:  # noqa: BLE001
                                logger.exception(
                                    "notify_step_done({}) failed", project_id
                                )
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
