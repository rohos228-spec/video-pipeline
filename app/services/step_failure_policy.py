"""Политика отказов шага.

Схема (на один running-шаг):
  - каждый fail → soft retry (файлы на диске не трогаем);
  - глобальный счётчик total_fails: 1…9;
  - каждые 3 fail (3, 6) → сон 30 мин, затем новый цикл;
  - на 9-м fail (3 цикла × 3 попытки) → paused + следующий в gen_queue.

Явный сброс шага — только через UI (reset_step), не при ошибках.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.chrome_recovery import (
    clear_chrome_recovery,
    handle_chrome_step_failure,
    is_chrome_infra_error,
)
from app.models import Project, ProjectStatus
from app.telegram.menu import step_by_running_status

FAILS_PER_CYCLE = 3
MAX_CYCLES = 3
MAX_TOTAL_FAILS = FAILS_PER_CYCLE * MAX_CYCLES  # 9
SLEEP_MINUTES = 30
# Лишние листы GPT / hotfix normalize — не морозить на полчаса.
XLSX_SHEET_FORMAT_SLEEP_MINUTES = 2


def sleep_minutes_for_error(error: Exception) -> int:
    msg = str(error)
    if "скачанный xlsx невалиден" in msg and "листы" in msg:
        return XLSX_SHEET_FORMAT_SLEEP_MINUTES
    # Неполный TSV / prose без TSV — книга не тронута; не морозить 30 мин.
    if (
        "всё ещё неполный" in msg
        or "CONTINUE_XLSX" in msg
        or "не вернула TSV" in msg
        or "project.xlsx не изменён" in msg
        or "без writeback" in msg
    ):
        return XLSX_SHEET_FORMAT_SLEEP_MINUTES
    conn_markers = (
        "Cannot connect to host",
        "ClientConnectorError",
        "CDP не доступен",
        "29229",
        "connect call failed",
    )
    if any(m in msg for m in conn_markers):
        return XLSX_SHEET_FORMAT_SLEEP_MINUTES
    return SLEEP_MINUTES


def _meta(project: Project) -> dict[str, Any]:
    m = project.meta
    return dict(m) if isinstance(m, dict) else {}


def _failure_state(project: Project) -> dict[str, Any]:
    meta = _meta(project)
    fs = meta.get("step_failure")
    return dict(fs) if isinstance(fs, dict) else {}


def _save_failure_state(project: Project, fs: dict[str, Any]) -> None:
    meta = _meta(project)
    meta["step_failure"] = fs
    project.meta = meta


def failure_sleep_until(project: Project) -> str | None:
    until = _failure_state(project).get("sleep_until")
    return str(until) if until else None


def is_sleeping(project: Project) -> bool:
    fs = _failure_state(project)
    until = fs.get("sleep_until")
    if not until:
        return False
    try:
        dt = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
    except ValueError:
        return False
    if datetime.now(timezone.utc) >= dt:
        return False
    return True


def clear_failure_backoff_for_manual_start(project: Project, *, running_key: str) -> bool:
    """Ручной запуск шага — снять sleep и счётчик fails для этого running-статуса."""
    fs = _failure_state(project)
    if not fs:
        return False
    changed = False
    if fs.pop("sleep_until", None) is not None:
        changed = True
    if fs.pop("sleep_paused", None) is not None:
        changed = True
    totals: dict[str, int] = dict(fs.get("total_fails") or {})
    if totals.pop(running_key, None) is not None:
        changed = True
        fs["total_fails"] = totals
    if not changed:
        return False
    _save_failure_state(project, fs)
    return True


def clear_failure_sleep(project: Project) -> bool:
    """Снять sleep_until (⏹ STOP) — без сброса счётчиков fails."""
    fs = _failure_state(project)
    if fs.pop("sleep_until", None) is None:
        return False
    _save_failure_state(project, fs)
    return True


def clear_sleep_if_expired(project: Project) -> bool:
    """Снять sleep_until если время вышло. True если только что проснулись."""
    if is_sleeping(project):
        return False
    fs = _failure_state(project)
    if not fs.get("sleep_until"):
        return False
    fs.pop("sleep_until", None)
    _save_failure_state(project, fs)
    return True


async def _soft_retry_without_wipe(
    session: AsyncSession,
    project: Project,
    step_code: str,
) -> None:
    """Повтор шага без reset_step / wipe — сохраняем файлы на диске."""
    if step_code == "audio":
        from app.services.artifact_recovery import recover_before_assemble

        await recover_before_assemble(session, project)
        logger.info(
            "[#{}] soft retry audio: подхват озвучки/whisper с диска (без wipe)",
            project.id,
        )
    elif step_code == "music":
        from app.services.artifact_recovery import recover_music_from_disk

        if await recover_music_from_disk(session, project):
            logger.info(
                "[#{}] soft retry music: music артефакт восстановлен с диска",
                project.id,
            )
        else:
            logger.info(
                "[#{}] soft retry music: без wipe, повтор генерации",
                project.id,
            )
    elif step_code == "assemble":
        from app.services.artifact_recovery import recover_before_assemble

        await recover_before_assemble(session, project)
        logger.info(
            "[#{}] soft retry assemble: подхват артефактов с диска (без wipe)",
            project.id,
        )
    elif step_code == "anim_pr":
        from app.services.animation_prompt_gpt import sync_animation_prompts_from_xlsx

        try:
            synced = await sync_animation_prompts_from_xlsx(session, project)
            logger.info(
                "[#{}] soft retry {}: synced {} animation_prompt из xlsx (без reset)",
                project.id,
                step_code,
                synced,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] sync_animation_prompts_from_xlsx on fail: {}",
                project.id,
                e,
            )
    elif step_code == "img":
        from app.services.scan_frames import sync_frames_with_disk_images

        synced = await sync_frames_with_disk_images(session, project)
        logger.info(
            "[#{}] soft retry img: {} кадров уже на диске (без wipe)",
            project.id,
            synced,
        )
    elif step_code == "video":
        from app.services.artifact_recovery import recover_scene_videos_from_disk

        recovered = await recover_scene_videos_from_disk(session, project)
        videos_dir = project.data_dir / "videos"
        on_disk = (
            len(list(videos_dir.glob("clip_*.mp4"))) if videos_dir.is_dir() else 0
        )
        logger.info(
            "[#{}] soft retry video: {} clip на диске, {} newly linked (без wipe)",
            project.id,
            on_disk,
            len(recovered),
        )
    else:
        logger.info(
            "[#{}] soft retry {}: без wipe, только restart",
            project.id,
            step_code,
        )


async def record_step_failure(
    session: AsyncSession,
    project: Project,
    *,
    error: Exception,
) -> str:
    """Обработать ошибку advance_project.

    Returns: retry | sleep | abandon | pause_infra | stopped
    """
    if is_chrome_infra_error(error):
        return await handle_chrome_step_failure(session, project, error)

    from app.services.error_catalog import describe_error
    from app.services.gen_queue_run import is_user_stopped
    from app.services.project_state import is_running_status
    from app.services.step_cancel import is_stop_requested

    # SQLite busy / PendingRollback при parallel projects — не копить к 30-мин sleep.
    err_code_early, err_msg_early = describe_error(error)
    # 402 / нет кредитов — не soft-retry и не sleep 30 мин (только жечь баланс).
    try:
        from app.services.scene_design.agent_chunks import is_credits_failure
    except Exception:  # noqa: BLE001
        is_credits_failure = lambda _e: False  # noqa: E731
    if is_credits_failure(error) or "credits insufficient" in (err_msg_early or "").lower():
        from app.services.project_control import pause_project as pause_project_svc
        from app.services.run_sync import mark_running_node_failed

        running = project.status
        step = step_by_running_status(running)
        fs = _failure_state(project)
        fs["last_error"] = err_msg_early
        fs["last_error_code"] = err_code_early or "gpt_credits"
        fs["last_running"] = running.value
        fs.pop("sleep_until", None)
        _save_failure_state(project, fs)
        await mark_running_node_failed(
            session,
            project,
            error,
            initiator="worker",
            error_code=err_code_early or "gpt_credits",
        )
        try:
            from app.services.scene_design import runner as sd_runner

            sd_runner.mark_failed(project, err_msg_early)
        except Exception:  # noqa: BLE001
            pass
        await pause_project_svc(session, project)
        await session.flush()
        logger.error(
            "[#{}] GPT credits exhausted (402) — pause, no soft-retry: {}",
            project.id,
            err_msg_early[:200],
        )
        return "pause_infra"

    if err_code_early == "infra_db_locked":
        running = project.status
        step = step_by_running_status(running)
        step_code = step.code if step else running.value
        await _soft_retry_without_wipe(session, project, step_code)
        fs = _failure_state(project)
        fs["last_error"] = err_msg_early
        fs["last_error_code"] = err_code_early
        fs["last_running"] = running.value
        fs.pop("sleep_until", None)
        _save_failure_state(project, fs)
        if step is not None:
            project.status = step.running_status
        from app.services.run_sync import update_active_node_progress_text

        await update_active_node_progress_text(
            session,
            project,
            f"БД занята — повтор без счётчика: {err_msg_early[:80]}",
        )
        await session.flush()
        logger.warning(
            "[#{}] infra_db_locked: soft retry без fail-счётчика (не sleep)",
            project.id,
        )
        return "retry"

    # ⏹ уже нажат — не поднимать planning снова soft-retry'ем (иначе крутилка вечная)
    if is_user_stopped(project) or is_stop_requested(project.id):
        from app.services.run_sync import mark_running_node_failed

        running = project.status
        step = step_by_running_status(running)
        err_code, err_msg = describe_error(error)
        fs = _failure_state(project)
        fs["last_error"] = err_msg
        fs["last_error_code"] = err_code
        if step is not None:
            fs["last_running"] = step.running_status.value
        fs.pop("sleep_until", None)
        _save_failure_state(project, fs)
        await mark_running_node_failed(
            session,
            project,
            error,
            initiator="worker",
            error_code=err_code,
        )
        if is_running_status(project.status):
            rollback = (
                step.requires
                if step is not None and step.requires is not None
                else ProjectStatus.new
            )
            project.status = rollback
        await session.flush()
        logger.warning(
            "[#{}] step failure ignored soft-retry (user_stop) → {}",
            project.id,
            project.status.value,
        )
        return "stopped"

    running = project.status
    step = step_by_running_status(running)
    step_code = step.code if step else running.value
    fs = _failure_state(project)
    key = running.value

    totals: dict[str, int] = dict(fs.get("total_fails") or {})
    total = totals.get(key, 0) + 1
    totals[key] = total
    fs["total_fails"] = totals

    err_code, err_msg = describe_error(error)
    fs["last_error"] = err_msg
    fs["last_error_code"] = err_code
    fs["last_running"] = key
    cycle = (total - 1) // FAILS_PER_CYCLE + 1
    fail_in_cycle = ((total - 1) % FAILS_PER_CYCLE) + 1

    await _soft_retry_without_wipe(session, project, step_code)

    if total >= MAX_TOTAL_FAILS:
        fs["abandoned_at"] = datetime.now(timezone.utc).isoformat()
        fs["recovery_cycles"] = MAX_CYCLES
        project.status = ProjectStatus.paused
        _save_failure_state(project, fs)
        from app.services.run_sync import mark_running_node_failed

        await mark_running_node_failed(
            session,
            project,
            error,
            initiator="worker",
            error_code=err_code,
        )
        await session.flush()
        logger.error(
            "[#{}] abandoned after {} fails ({} cycles) on {}",
            project.id,
            total,
            MAX_CYCLES,
            key,
        )
        return "abandon"

    if total % FAILS_PER_CYCLE == 0:
        sleep_min = sleep_minutes_for_error(error)
        until = datetime.now(timezone.utc) + timedelta(minutes=sleep_min)
        fs["sleep_until"] = until.isoformat()
        fs["recovery_cycles"] = total // FAILS_PER_CYCLE
        # Пометка «paused — наш, от сна»: maybe_resume_after_sleep по ней
        # отличает sleep-паузу от ручной паузы пользователя.
        fs["sleep_paused"] = True
        _save_failure_state(project, fs)
        from app.services.run_sync import mark_running_node_failed

        # Сначала failed по NodeRun (пока status ещё running-шаг), потом уходим
        # из planning/… — иначе UI крутит «выполняется» все 30 мин сна.
        await mark_running_node_failed(
            session,
            project,
            error,
            initiator="worker",
            error_code=err_code,
        )
        # Не откатывать в step.requires (часто enrich_N_ready): при auto_mode
        # auto_advance сразу уводит проект «назад» (например img_pr → enrich_3_ready → scripting).
        # Во время 30-мин сна держим paused; ручной run_step снимает sleep.
        project.status = ProjectStatus.paused
        await session.flush()
        logger.warning(
            "[#{}] sleep {} min after fail {}/{} on {} (cycle {}/{}) → status={}",
            project.id,
            sleep_min,
            total,
            MAX_TOTAL_FAILS,
            key,
            cycle,
            MAX_CYCLES,
            project.status.value,
        )
        return "sleep"

    _save_failure_state(project, fs)
    # Не вызываем start_step / reset_step — они удаляют файлы на диске.
    if step is not None:
        project.status = step.running_status
    short_err = err_msg.replace("\n", " ")[:120]
    from app.services.run_sync import update_active_node_progress_text

    await update_active_node_progress_text(
        session,
        project,
        f"Повтор {fail_in_cycle} из {FAILS_PER_CYCLE}: {short_err}",
    )
    await session.flush()
    logger.warning(
        "[#{}] fail {}/{} on {} (cycle {} fail {}/{}), soft retry без wipe: {}",
        project.id,
        total,
        MAX_TOTAL_FAILS,
        key,
        cycle,
        fail_in_cycle,
        FAILS_PER_CYCLE,
        fs["last_error"],
    )
    return "retry"


def clear_failure_on_success(project: Project, running: ProjectStatus) -> None:
    clear_chrome_recovery(project)
    fs = _failure_state(project)
    totals: dict[str, int] = dict(fs.get("total_fails") or {})
    totals.pop(running.value, None)
    fs["total_fails"] = totals
    fs.pop("sleep_until", None)
    fs.pop("sleep_paused", None)
    fs.pop("recovery_cycles", None)
    fs.pop("last_error", None)
    _save_failure_state(project, fs)


async def maybe_resume_after_sleep(
    session: AsyncSession,
    project: Project,
) -> bool:
    """После сна — снова запустить тот же шаг (auto retry).

    Сон ставит status=paused — поэтому paused НЕ повод выйти, если это
    наша sleep-пауза (fs.sleep_paused) и пользователь не жал паузу сам
    (meta.paused_from_status / user_stop).
    """
    if not clear_sleep_if_expired(project):
        return False
    from app.services.gen_queue_run import is_user_stopped

    if is_user_stopped(project):
        return False
    if not project.auto_mode:
        return False
    fs = _failure_state(project)
    if project.status is ProjectStatus.paused:
        meta = _meta(project)
        user_paused = "paused_from_status" in meta
        if user_paused or not fs.get("sleep_paused"):
            return False
    step_key = fs.get("last_running")
    if not step_key:
        return False
    try:
        running = ProjectStatus(step_key)
    except ValueError:
        return False
    step = step_by_running_status(running)
    if step is None:
        return False
    fs.pop("sleep_paused", None)
    _save_failure_state(project, fs)
    await _soft_retry_without_wipe(session, project, step.code)
    project.status = step.running_status
    await session.flush()
    logger.info("[#{}] resumed after sleep → {} (без wipe)", project.id, step.code)
    return True
