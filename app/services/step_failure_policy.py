"""Политика отказов шага.

Схема (на один running-шаг):
  - каждый fail → reset_step (обнуление, не «полный провал»);
  - глобальный счётчик total_fails: 1…9;
  - каждые 3 fail (3, 6) → сон 30 мин, затем новый цикл;
  - на 9-м fail (3 цикла × 3 попытки) → paused + следующий в gen_queue.

Итого до abandon: до 9 reset-попыток на шаге.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.services.project_steps import start_step
from app.services.reset_step import reset_step
from app.telegram.menu import step_by_running_status

FAILS_PER_CYCLE = 3

# При ошибке — не reset_step (не стирать прогресс), только sync xlsx + restart.
_SOFT_RETRY_STEP_CODES = frozenset({"anim_pr"})
MAX_CYCLES = 3
MAX_TOTAL_FAILS = FAILS_PER_CYCLE * MAX_CYCLES  # 9
SLEEP_MINUTES = 30


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
    err_msg = str(error)
    meta = _meta(project)
    from app.fleet.montage_handoff import is_fleet_hub_montage

    async def _abandon_no_retry(*, blocked_key: str, log_msg: str) -> str:
        fs = _failure_state(project)
        fs["last_error"] = f"{type(error).__name__}: {error}"
        fs["non_retryable"] = True
        meta[blocked_key] = err_msg
        meta.pop("montage_queue_enqueued", None)
        project.meta = meta
        project.status = ProjectStatus.paused
        _save_failure_state(project, fs)
        await session.flush()
        logger.error("[#{}] {}", project.id, log_msg)
        return "abandon"

    if running is ProjectStatus.paused or step is None:
        fs = _failure_state(project)
        fs["last_error"] = f"{type(error).__name__}: {error}"
        fs["non_retryable"] = True
        if is_fleet_hub_montage(project) and "montage_blocked" not in meta:
            meta["montage_blocked"] = err_msg
            project.meta = meta
        _save_failure_state(project, fs)
        await session.flush()
        logger.info(
            "[#{}] failure on {} — paused, no reset/retry",
            project.id,
            running.value,
        )
        return "abandon"

    if is_fleet_hub_montage(project) and (
        running is ProjectStatus.generating_audio
        or "fleet import" in err_msg
        or "не генерируем через 11Labs" in err_msg
    ):
        return await _abandon_no_retry(
            blocked_key="montage_blocked",
            log_msg=f"fleet audio blocked — paused (no reset): {err_msg}",
        )

    # Сборка без данных — retry бессмысленен (reset+start_step = бесконечный цикл).
    if (
        "сборка невозможна" in err_msg
        or (
            running is ProjectStatus.assembling
            and "не удалось прочитать текст кадров" in err_msg
        )
        or (
            running is ProjectStatus.assembling
            and "монтаж только по Excel" in err_msg
        )
        or (
            running is ProjectStatus.assembling
            and "нет кадров с voiceover" in err_msg
        )
    ):
        fs = _failure_state(project)
        fs["last_error"] = f"{type(error).__name__}: {error}"
        fs["non_retryable"] = True
        meta = _meta(project)
        meta["assemble_blocked"] = err_msg
        meta.pop("montage_queue_enqueued", None)
        project.meta = meta
        project.status = ProjectStatus.paused
        _save_failure_state(project, fs)
        await session.flush()
        logger.error(
            "[#{}] assemble blocked — paused (no auto-retry): {}",
            project.id,
            err_msg,
        )
        return "abandon"

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

    if step_code in _SOFT_RETRY_STEP_CODES:
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
    else:
        try:
            await reset_step(session, project, step_code)
        except Exception as e:  # noqa: BLE001
            logger.warning("[#{}] reset_step {} failed: {}", project.id, step_code, e)

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
        until = datetime.now(timezone.utc) + timedelta(minutes=SLEEP_MINUTES)
        fs["sleep_until"] = until.isoformat()
        fs["recovery_cycles"] = total // FAILS_PER_CYCLE
        _save_failure_state(project, fs)
        await session.flush()
        logger.warning(
            "[#{}] sleep {} min after fail {}/{} on {} (cycle {}/{})",
            project.id,
            SLEEP_MINUTES,
            total,
            MAX_TOTAL_FAILS,
            key,
            cycle,
            MAX_CYCLES,
        )
        return "sleep"

    _save_failure_state(project, fs)
    try:
        await start_step(session, project, step_code)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] start_step {} after fail {}/{}: {}",
            project.id,
            step_code,
            total,
            MAX_TOTAL_FAILS,
            e,
        )
    await session.flush()
    logger.warning(
        "[#{}] fail {}/{} on {} (cycle {} fail {}/{}), step reset+restart: {}",
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
    await start_step(session, project, step.code)
    await session.flush()
    logger.info("[#{}] resumed after sleep → {}", project.id, step.code)
    return True
