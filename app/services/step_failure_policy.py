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
    totals: dict[str, int] = dict(fs.get("total_fails") or {})
    if totals.pop(running_key, None) is not None:
        changed = True
        fs["total_fails"] = totals
    if not changed:
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
        logger.info(
            "[#{}] soft retry video: {} clip на диске (без wipe)",
            project.id,
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

    Returns: retry | sleep | abandon
    """
    running = project.status
    step = step_by_running_status(running)
    step_code = step.code if step else running.value
    fs = _failure_state(project)
    key = running.value

    totals: dict[str, int] = dict(fs.get("total_fails") or {})
    total = totals.get(key, 0) + 1
    totals[key] = total
    fs["total_fails"] = totals
    fs["last_error"] = f"{type(error).__name__}: {error}"
    fs["last_running"] = key
    cycle = (total - 1) // FAILS_PER_CYCLE + 1
    fail_in_cycle = ((total - 1) % FAILS_PER_CYCLE) + 1

    await _soft_retry_without_wipe(session, project, step_code)

    if total >= MAX_TOTAL_FAILS:
        fs["abandoned_at"] = datetime.now(timezone.utc).isoformat()
        fs["recovery_cycles"] = MAX_CYCLES
        project.status = ProjectStatus.paused
        _save_failure_state(project, fs)
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
        _save_failure_state(project, fs)
        await session.flush()
        logger.warning(
            "[#{}] sleep {} min after fail {}/{} on {} (cycle {}/{})",
            project.id,
            sleep_min,
            total,
            MAX_TOTAL_FAILS,
            key,
            cycle,
            MAX_CYCLES,
        )
        return "sleep"

    _save_failure_state(project, fs)
    # Не вызываем start_step / reset_step — они удаляют файлы на диске.
    if step is not None:
        project.status = step.running_status
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
    fs = _failure_state(project)
    totals: dict[str, int] = dict(fs.get("total_fails") or {})
    totals.pop(running.value, None)
    fs["total_fails"] = totals
    fs.pop("sleep_until", None)
    fs.pop("recovery_cycles", None)
    fs.pop("last_error", None)
    _save_failure_state(project, fs)


async def maybe_resume_after_sleep(
    session: AsyncSession,
    project: Project,
) -> bool:
    """После сна — снова запустить тот же шаг (auto retry)."""
    if not clear_sleep_if_expired(project):
        return False
    if not project.auto_mode or project.status is ProjectStatus.paused:
        return False
    fs = _failure_state(project)
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
    await _soft_retry_without_wipe(session, project, step.code)
    project.status = step.running_status
    await session.flush()
    logger.info("[#{}] resumed after sleep → {} (без wipe)", project.id, step.code)
    return True
