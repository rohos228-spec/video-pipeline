"""Entrypoint: воркер + опционально Telegram + веб-API в одном процессе.

Запуск:
    python -m app.main

Что делает:
  1. Создаёт таблицы БД (SQLite), если их ещё нет.
  2. Синкует мастер-промты из `prompts/*.vN.md` в БД.
  3. Если `TELEGRAM_ENABLED` и токен заданы — aiogram-поллинг (HITL в TG).
  4. Фоновый воркер продвигает проекты; HITL без TG — через веб (:8765).
"""

from __future__ import annotations

import asyncio
import contextlib

from loguru import logger

from app.db import engine
from app.models import Base, Project, ProjectStatus
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
            ("image_quality", "VARCHAR(10)"),
            ("image_relax", "BOOLEAN DEFAULT 0"),
            ("video_generator", "VARCHAR(40)"),
            ("video_resolution", "VARCHAR(10)"),
            ("video_relax", "BOOLEAN DEFAULT 0"),
            ("hero_count", "INTEGER"),
            ("hero_descriptions", "JSON"),
            ("hero_variations", "JSON"),
            ("hero_variation_modifiers", "JSON"),
            ("prompt_overrides", "JSON"),
            ("gpt_text_overrides", "JSON"),
            # Pipeline-redesign: «Объекты» (Персонажи+Предметы) и слоты
            # «Доп работа с EXCEL».
            ("enrich_slots_count", "INTEGER DEFAULT 3"),
            ("item_descriptions", "JSON"),
            ("item_variations", "JSON"),
            # Массовое создание: каждая запись projects может принадлежать
            # массовому проекту (BatchProject). batch_slug дублирован для
            # быстрого построения data_dir без join к batch_projects.
            ("batch_id", "INTEGER"),
            ("batch_position", "INTEGER"),
            ("batch_slug", "VARCHAR(120)"),
            ("auto_mode", "BOOLEAN DEFAULT 0"),
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

        # Миграция со статуса `failed` (его больше не используем): просто
        # сбрасываем в `new`, дальше recompute_all поднимет до правильного
        # уровня по данным.
        try:
            await conn.exec_driver_sql(
                "UPDATE projects SET status = 'new' WHERE status = 'failed'"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("migrate failed→new: {}", e)


async def _backfill_from_disk() -> None:
    """ROOT FIX: подтягиваем project.xlsx и voiceover.txt в БД для всех
    проектов перед recompute. xlsx-flow-шаги (_run_plan_xlsx /
    _run_script_xlsx / _run_split_xlsx) долгое время не сохраняли
    `project.general_plan` / `project.script_text` / Frame'ы — данные
    жили только в xlsx/txt на диске. После рестарта `compute_actual_status`
    видел пустые поля и сбрасывал status назад («всё откатилось»).

    Этот бэкфилл — идемпотентный: если поля уже заполнены и совпадают
    с xlsx, ничего не меняется.
    """
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Project
    from app.services.chatgpt_xlsx import sync_project_xlsx

    try:
        async with session_scope() as s:
            projects = (
                await s.execute(select(Project))
            ).scalars().all()
            for p in projects:
                # p.data_dir автоматически даёт правильный путь:
                # для одиночных — data/videos/<slug>/,
                # для батч-подпроектов — data/batches/<batch_slug>/sub/<slug>/.
                proj_dir = p.data_dir
                proj_xlsx = proj_dir / "project.xlsx"
                voiceover_txt = proj_dir / "voiceover.txt"

                # 1) Подтягиваем xlsx → DB.
                #
                # Сначала пробуем v8-импортёр (лист «Общий план»,
                # лист «план», R49). У v8-шаблона СВОЯ структура,
                # отличная от старой колоночной (SHEET_GENERAL = «Общий
                # план ролика», SHEET_FRAMES = «Кадры»).
                #
                # keep_fields=True — НЕ перезаписываем непустые
                # general_plan / script_text. Бэкфилл только заполняет
                # пустоты.
                if proj_xlsx.exists():
                    try:
                        info = await sync_project_xlsx(
                            s, p, proj_xlsx, keep_fields=True
                        )
                        if info and (
                            info.get("project_fields_changed")
                            or info.get("frames_created")
                            or info.get("frames_updated")
                            or info.get("frames_changed")
                        ):
                            logger.info(
                                "backfill[#{}]: xlsx → DB: {}", p.id, info
                            )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "backfill[#{}]: sync_project_xlsx failed: {}",
                            p.id,
                            e,
                        )

                # 2) voiceover.txt → project.script_text (xlsx-flow шага 2
                # сохраняет туда, а не в xlsx).
                if voiceover_txt.exists() and not p.script_text:
                    try:
                        txt = voiceover_txt.read_text(
                            encoding="utf-8"
                        ).strip()
                        if txt:
                            p.script_text = txt
                            logger.info(
                                "backfill[#{}]: voiceover.txt → "
                                "project.script_text ({} симв)",
                                p.id, len(txt),
                            )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "backfill[#{}]: voiceover.txt read failed: {}",
                            p.id, e,
                        )
    except Exception as e:  # noqa: BLE001
        logger.warning("backfill on startup failed: {}", e)


async def _recompute_all_projects() -> None:
    """ROOT FIX: на каждом старте перевычисляем status для ВСЕХ проектов
    из реальных данных в БД. Лечит десинхронизацию (status=hero_ready при
    отсутствии frames и т.п. — последствие старого failed-bypass)."""
    from app.db import session_scope
    from app.services.project_state import recompute_all

    try:
        async with session_scope() as s:
            changes = await recompute_all(s)
            if changes:
                logger.warning(
                    "recompute: {} проект(а/ов) с десинхронизацией статуса "
                    "→ {}",
                    len(changes),
                    {pid: f"{old}→{new}" for pid, (old, new) in changes.items()},
                )
            else:
                logger.info("recompute: все проекты в консистентном статусе")
    except Exception as e:  # noqa: BLE001
        logger.warning("recompute on startup failed: {}", e)


def _running_status_requires(
    running_status: ProjectStatus,
    project: Project | None = None,
) -> ProjectStatus | None:
    """Найти `requires` шага, чей running_status == running_status.

    Используется при анти-зацикливании: чтобы откатить упавший проект
    к prerequisite предыдущего шага (а не в тупиковый `failed`).

    Для `generating_hero` после enrich-слотов откатываемся к последнему
    `enrich_N_ready`, а не к `frames_ready` — иначе граф заново запустит
    уже пройденные enrich-шаги.
    """
    # Импорт внутри функции, чтобы избежать кругового импорта на старте.
    from app.models import ProjectStatus as PS
    from app.telegram.menu import step_by_running_status

    if running_status is PS.generating_hero and project is not None:
        meta = project.meta if isinstance(project.meta, dict) else {}
        slots: list[int] = []
        for raw in meta.get("enrich_completed_slots") or []:
            try:
                slots.append(int(raw))
            except (TypeError, ValueError):
                continue
        if slots:
            slot_ready = {
                1: PS.enrich_1_ready,
                2: PS.enrich_2_ready,
                3: PS.enrich_3_ready,
                4: PS.enrich_4_ready,
                5: PS.enrich_5_ready,
            }.get(max(slots))
            if slot_ready is not None:
                return slot_ready

    step = step_by_running_status(running_status)
    return step.requires if step is not None else None


async def _run_worker_loop(bot) -> None:  # Bot | NoopBot
    """Фоновая петля воркера: сканирует БД и продвигает проекты.

    Анти-зацикливание: если один и тот же шаг падает >= MAX_FAIL раз
    подряд, откатываем статус проекта на prerequisite предыдущего шага
    и шлём в TG уведомление. РАНЬШЕ тут стоял `status = failed`, но
    `failed` лочил всё меню (все шаги ⬜, никуда не ткнуть) — поэтому
    отказались от него.
    До MAX_FAIL шлём только первое сообщение на каждый новый шаг
    (чтобы не спамить одинаковыми ошибками).
    """
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Project, ProjectStatus
    from app.services.advance_runner import advance_project_job
    from app.services.step_cancel import (
        StepCancelledError,
        is_generation_active,
        register_advance_task,
        unregister_advance_task,
    )

    # (project_id, status.value) -> кол-во подряд неудач на этом шаге
    fail_counts: dict[tuple[int, str], int] = {}

    # Воркер запускает только «running»-статусы. «ready»-статусы — это
    # ожидание действия пользователя из TG-меню, авто-advance отключён.
    # ВАЖНО: список должен содержать ВСЕ running-статусы из ProjectStatus,
    # иначе воркер не подхватит шаг и юзер увидит «бесконечно выполняется».
    # Маппинг running-статус → handler смотри в `pipeline.advance_project`.
    active = [
        ProjectStatus.planning,
        ProjectStatus.scripting,
        ProjectStatus.splitting,
        ProjectStatus.generating_hero,
        ProjectStatus.generating_items,
        ProjectStatus.enriching_1,
        ProjectStatus.enriching_2,
        ProjectStatus.enriching_3,
        ProjectStatus.enriching_4,
        ProjectStatus.enriching_5,
        ProjectStatus.generating_image_prompts,
        ProjectStatus.generating_images,
        ProjectStatus.generating_animation_prompts,
        ProjectStatus.generating_videos,
        ProjectStatus.generating_music,
        ProjectStatus.generating_audio,
        ProjectStatus.assembling,
        ProjectStatus.publishing,
    ]
    from app.services.mass_pause import is_active as _mass_pause_active
    from app.services.step_cancel import is_stop_requested
    from app.telegram.bot import notify_step_done

    _last_mass_pause_log = False
    while True:
        # Пауза МАССОВОЙ генерации (маркер `data/.mass_pause`):
        # пропускаем serial_tick_batches и auto_advance подпроектов с
        # `batch_id != NULL`. Индивидуальные проекты продолжают работать
        # как обычно. Сами running-шаги batch-подпроектов (planning/
        # scripting/...) не прерываем — дорабатывают до *_ready, дальше
        # auto_advance их уже не двинет пока пауза.
        mass_paused = _mass_pause_active()
        if mass_paused and not _last_mass_pause_log:
            logger.info("worker: mass pause active — batches frozen, individual projects keep running")
            _last_mass_pause_log = True
        elif not mass_paused and _last_mass_pause_log:
            logger.info("worker: mass pause снята")
            _last_mass_pause_log = False
        try:
            async with session_scope() as s:
                projects = (
                    await s.execute(select(Project).where(Project.status.in_(active)))
                ).scalars().all()
                for p in projects:
                    if is_stop_requested(p.id):
                        from app.services.project_control import stop_project_running

                        info = await stop_project_running(s, p)
                        if info["ok"]:
                            await s.commit()
                            from app.services.run_sync import sync_run_for_project

                            await sync_run_for_project(p.id)
                            logger.info(
                                "worker: ⏹ #{} — {}",
                                p.id,
                                info["message"],
                            )
                        continue
                    if is_generation_active(p.id):
                        logger.debug(
                            "worker: #{} {} — шаг уже выполняется, пропуск тика",
                            p.id,
                            p.status.value,
                        )
                        continue
                    from app.services.step_failure_policy import (
                        clear_failure_on_success,
                        is_sleeping,
                        maybe_resume_after_sleep,
                        record_step_failure,
                    )

                    if is_sleeping(p):
                        from app.services.step_failure_policy import failure_sleep_until

                        logger.info(
                            "worker: #{} {} — пауза после ошибок до {}, пропуск тика",
                            p.id,
                            p.status.value,
                            failure_sleep_until(p),
                        )
                        await maybe_resume_after_sleep(s, p)
                        if is_sleeping(p):
                            continue
                    key = (p.id, p.status.value)
                    prev_status_value = p.status.value
                    project_id = p.id
                    task = asyncio.create_task(
                        advance_project_job(project_id, bot)
                    )
                    register_advance_task(project_id, task)
                    try:
                        result = await task
                        clear_failure_on_success(p, ProjectStatus(prev_status_value))
                        fail_counts.pop(key, None)
                        if result.new_status is not None:
                            with contextlib.suppress(Exception):
                                await s.refresh(p)
                            try:
                                from app.services.gen_queue import (
                                    is_timeline_complete,
                                    on_project_timeline_maybe_advance_queue,
                                )

                                if await is_timeline_complete(s, p):
                                    started = await on_project_timeline_maybe_advance_queue(
                                        s, p
                                    )
                                    if started:
                                        await s.commit()
                            except Exception:  # noqa: BLE001
                                logger.exception(
                                    "gen_queue advance after step failed for #{}",
                                    project_id,
                                )
                            try:
                                await notify_step_done(
                                    bot,
                                    project_id,
                                    result.prev_status,
                                    result.new_status,
                                )
                            except Exception:  # noqa: BLE001
                                logger.exception(
                                    "notify_step_done({}) failed", project_id
                                )
                    except (StepCancelledError, asyncio.CancelledError):
                        # ⏹ Остановить — task.cancel() или кооперативный выход.
                        logger.info(
                            "[#{}] advance_project cancelled by user (⏹)",
                            project_id,
                        )
                        fail_counts.pop(key, None)
                    except Exception as e:  # noqa: BLE001
                        logger.exception("advance_project failed for #{}", p.id)
                        prev = fail_counts.get(key, 0)
                        fail_counts[key] = prev + 1
                        try:
                            from app.services.step_failure_policy import (
                                record_step_failure,
                            )

                            action = await record_step_failure(s, p, error=e)
                            await s.commit()
                            if bot and settings.telegram_active:
                                if action == "retry" and prev == 0:
                                    msg = (
                                        f"⚠️ #{p.id} ({p.status.value}): "
                                        f"{type(e).__name__}: {e}"
                                    )
                                    await bot.send_message(
                                        settings.telegram_owner_chat_id, msg[:3800]
                                    )
                                elif action == "sleep":
                                    await bot.send_message(
                                        settings.telegram_owner_chat_id,
                                        (
                                            f"😴 #{p.id}: 3 ошибки — reset, "
                                            f"пауза 30 мин (макс. 9 попыток)"
                                        )[:3800],
                                    )
                                elif action == "abandon":
                                    await bot.send_message(
                                        settings.telegram_owner_chat_id,
                                        (
                                            f"🛑 #{p.id}: 3 цикла отказов — "
                                            f"paused, следующий в очереди"
                                        )[:3800],
                                    )
                                    from app.services.gen_queue import gen_queue_tick

                                    await gen_queue_tick(s)
                                    await s.commit()
                            fail_counts.pop(key, None)
                        except Exception:  # noqa: BLE001
                            logger.warning(
                                "step_failure_policy failed for #{}", p.id
                            )
                    finally:
                        unregister_advance_task(project_id)

                # --- auto_mode ---
                # 1) auto-advance: для auto_mode проектов в *_ready
                #    статусе запускаем GPT-чек / авто-апруф.
                try:
                    from app.orchestrator.auto_advance import (
                        TRANSITIONS,
                        maybe_auto_advance,
                        serial_tick_batches,
                        serial_tick_mass_lanes,
                    )

                    ready_statuses = list(TRANSITIONS.keys())
                    auto_projects = (
                        await s.execute(
                            select(Project).where(
                                Project.auto_mode == True,  # noqa: E712
                                Project.status.in_(ready_statuses),
                            )
                        )
                    ).scalars().all()
                    for ap in auto_projects:
                        if mass_paused and ap.batch_id is not None:
                            continue
                        if is_generation_active(ap.id):
                            continue
                        prev = ap.status.value
                        try:
                            advanced = await maybe_auto_advance(s, ap, bot)
                        except Exception:  # noqa: BLE001
                            logger.exception(
                                "auto_advance failed for #{}", ap.id
                            )
                            continue
                        if advanced and ap.status.value != prev:
                            new_status = ap.status.value
                            project_id = ap.id
                            await s.commit()
                            try:
                                from app.services.gen_queue import (
                                    on_project_timeline_maybe_advance_queue,
                                )

                                started = await on_project_timeline_maybe_advance_queue(
                                    s, ap
                                )
                                if started:
                                    await s.commit()
                            except Exception:  # noqa: BLE001
                                logger.exception(
                                    "gen_queue advance after auto_advance failed for #{}",
                                    ap.id,
                                )
                            try:
                                await notify_step_done(
                                    bot, project_id, prev, new_status
                                )
                            except Exception:  # noqa: BLE001
                                logger.exception(
                                    "notify_step_done({}) failed", project_id
                                )

                    # 2) serial worker: запускает следующий подпроект
                    #    из активного массового, если нет «занятого».
                    #    При паузе массовой — вообще не вызываем.
                    if not mass_paused:
                        try:
                            started = await serial_tick_batches(s)
                            if started:
                                await s.commit()
                        except Exception:  # noqa: BLE001
                            logger.exception("serial_tick_batches failed")
                        try:
                            mass_started = await serial_tick_mass_lanes(s)
                            if mass_started:
                                await s.commit()
                        except Exception:  # noqa: BLE001
                            logger.exception("serial_tick_mass_lanes failed")
                        try:
                            from app.services.gen_queue import gen_queue_tick

                            gq = await gen_queue_tick(s)
                            if gq:
                                await s.commit()
                        except Exception:  # noqa: BLE001
                            logger.exception("gen_queue_tick failed")
                except Exception:  # noqa: BLE001
                    logger.exception("auto_mode tick failed")
        except Exception:  # noqa: BLE001
            logger.exception("worker loop iteration failed")
        await asyncio.sleep(5)


async def _await_background_tasks(tasks: list[asyncio.Task]) -> None:
    """Держит процесс живым, пока работают фоновые задачи.

    Web-only (без Telegram): worker + uvicorn + sync — бесконечные петли.
    `gather` ждёт все сразу и не отменяет их, если одна «тихо» завершилась
    (что случалось с uvicorn при FIRST_COMPLETED → UI сразу падал).

    С Telegram: когда поллинг завершился — гасим остальное (старое поведение).
    """
    if not tasks:
        return
    if settings.web_enabled and not settings.telegram_active:
        await asyncio.gather(*tasks)
        return
    # FIRST_COMPLETED, а не FIRST_EXCEPTION: воркер ловит исключения внутри
    # петли; FIRST_EXCEPTION ждал бы вечно, если поллинг завершится штатно.
    done, pending = await asyncio.wait(
        tasks, return_when=asyncio.FIRST_COMPLETED
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


async def _startup_maintenance() -> None:
    """Тяжёлая инициализация в фоне — не блокирует /api/health."""
    try:
        from app.db import session_scope
        from app.services.local_library import ensure_library_dirs, import_existing_prompts

        ensure_library_dirs()
        async with session_scope() as s:
            info = await import_existing_prompts(s)
            logger.info("local library import: {}", info)
        await _backfill_from_disk()
        await _recompute_all_projects()
        await sync_prompts_from_files()
        from app.services.default_project import ensure_default_project

        await ensure_default_project()
    except Exception as e:  # noqa: BLE001
        logger.exception("startup maintenance failed: {}", e)


async def main() -> None:
    import sys

    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                with contextlib.suppress(Exception):
                    reconfigure(encoding="utf-8", errors="replace")

    logger.info(
        "starting video-pipeline, owner chat_id={}, db={}",
        settings.telegram_owner_chat_id,
        settings.db_url,
    )
    await _init_db()

    from app.db import session_scope
    from app.services.startup_guard import block_pipeline_autorun_on_startup

    async with session_scope() as s:
        guard_stats = await block_pipeline_autorun_on_startup(s)
        logger.warning("startup autorun guard: {}", guard_stats)

    # Backfill/recompute могут занимать 30–60+ сек на большой БД.
    # Поднимаем HTTP сразу после init_db, чтобы Launcher не ждал таймаут.
    maintenance_task = asyncio.create_task(_startup_maintenance())

    from app.telegram.noop_bot import get_worker_bot

    real_bot = None
    polling_task: asyncio.Task | None = None
    if settings.telegram_active:
        real_bot, _ = await build_bot()
        logger.info("telegram bot polling started")
        polling_task = asyncio.create_task(
            dp.start_polling(
                real_bot, allowed_updates=dp.resolve_used_update_types()
            )
        )
    else:
        logger.info(
            "telegram disabled — web-only mode (HITL и шаги через http://{}:{})",
            settings.web_host,
            settings.web_port,
        )

    worker_bot = get_worker_bot(real_bot)
    from app.services.pipeline_worker import ensure_pipeline_worker_started

    ensure_pipeline_worker_started(worker_bot)
    tasks: list[asyncio.Task] = [maintenance_task]
    from app.services.pipeline_worker import get_pipeline_worker_task

    worker_task = get_pipeline_worker_task()
    if worker_task is not None:
        tasks.append(worker_task)
    if polling_task is not None:
        tasks.insert(0, polling_task)
    logger.info("background worker started")
    try:
        from app.bots.chatgpt import CHATGPT_ATTACH_LOGIC_ID
        from app.web.studio_version import read_studio_version

        sv = read_studio_version()
        logger.info(
            "Studio UI {} | ChatGPT attach={} | backend_ok={}",
            sv.get("label"),
            CHATGPT_ATTACH_LOGIC_ID,
            sv.get("backend_ok"),
        )
        if sv.get("backend_ok") is False:
            logger.warning(
                "Backend attach mismatch: running={}, expected={} — "
                "перезапустите Python (Launcher: 4 Stop → 2 Start Studio)",
                sv.get("backend_attach"),
                sv.get("attach_expected"),
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("studio version log failed: {}", e)

    # Локальный веб-UI (FastAPI + WS) — поднимается в этом же процессе.
    web_task: asyncio.Task | None = None
    if settings.web_enabled:
        from app.services.run_sync import background_sync_loop
        from app.web import create_app

        web_app = create_app()
        import uvicorn

        config = uvicorn.Config(
            web_app,
            host=settings.web_host,
            port=settings.web_port,
            log_level=settings.log_level.lower(),
            access_log=False,
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        web_task = asyncio.create_task(server.serve())
        sync_task = asyncio.create_task(background_sync_loop())
        tasks.append(web_task)
        tasks.append(sync_task)
        logger.info(
            "web UI: http://{}:{} (REST на /api/*, WS на /ws/{{channel}}) — "
            "backfill/recompute в фоне",
            settings.web_host,
            settings.web_port,
        )

    try:
        await _await_background_tasks(tasks)
    finally:
        if real_bot is not None:
            await real_bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
