"""Политика отказов шага.

Схема (на один running-шаг):
  - каждый fail → soft retry (файлы на диске не трогаем);
  - глобальный счётчик total_fails: 1…9;
  - каждые 3 fail (3, 6) → сон 30 мин, затем новый цикл;
  - на 9-м fail (3 цикла × 3 попытки) → paused + следующий в gen_queue.

Явный сброс шага — только через UI (reset_step), не при ошибках.

Профиль ожидания (C08). По умолчанию «safe» - схема выше без изменений. Профиль «fast» сокращает
ожидание: 2 отказа x 2 цикла = 4 отказа, сон 3 мин (формат-ошибки и обрыв связи - 1 мин), а ошибки
настройки, которые повтор не вылечит (нет ключа, неверный ключ, модель не разрешена), закрывают шаг сразу.
Профиль выбирает переменная окружения STEP_FAILURE_WAIT_PROFILE или ``project.meta["step_failure_profile"]``
(приоритет у проекта). Профиль меняет ТОЛЬКО число и длину ожиданий: повтор остаётся «мягким» (файлы не
трогаем), платный повтор после исчерпания решает человек/политика, а не профиль.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
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
INFRA_LOCK_MAX_STREAK = 20  # подряд «БД занята» без прогресса -> пауза
SLEEP_MINUTES = 30
# Лишние листы GPT / hotfix normalize — не морозить на полчаса.
XLSX_SHEET_FORMAT_SLEEP_MINUTES = 2


WAIT_PROFILE_ENV = "STEP_FAILURE_WAIT_PROFILE"
WAIT_PROFILE_SAFE = "safe"
WAIT_PROFILE_FAST = "fast"
WAIT_PROFILE_NAMES = (WAIT_PROFILE_SAFE, WAIT_PROFILE_FAST)
FAST_FAILS_PER_CYCLE = 2
FAST_MAX_CYCLES = 2
FAST_SLEEP_MINUTES = 3
FAST_QUICK_SLEEP_MINUTES = 1
# Ошибки настройки: ключа/доступа/модели нет, повтор через минуты их не лечит. Только профиль fast
# закрывает шаг на первом таком отказе; в safe поведение прежнее.
FATAL_CONFIG_ERROR_CODES = frozenset({
    "gpt_no_key", "gpt_no_base", "gpt_auth", "gpt_model_unauthorized", "gpt_model_unsupported",
    "media_no_key", "media_auth",
})


@dataclass(frozen=True)
class WaitProfile:
    name: str
    fails_per_cycle: int
    max_cycles: int
    sleep_minutes: int
    quick_sleep_minutes: int
    abandon_on_fatal_config: bool

    @property
    def max_total_fails(self) -> int:
        return self.fails_per_cycle * self.max_cycles


def wait_profile(project: Project | None = None) -> WaitProfile:
    """Профиль ожидания. Значения safe читаются из модуля в момент вызова (их подменяют стенды)."""
    name = ""
    if project is not None:
        meta = project.meta if isinstance(project.meta, dict) else {}
        name = str(meta.get("step_failure_profile") or "").strip().lower()
    if name not in WAIT_PROFILE_NAMES:
        name = (os.environ.get(WAIT_PROFILE_ENV) or "").strip().lower()
    if name == WAIT_PROFILE_FAST:
        return WaitProfile(WAIT_PROFILE_FAST, FAST_FAILS_PER_CYCLE, FAST_MAX_CYCLES,
                           FAST_SLEEP_MINUTES, FAST_QUICK_SLEEP_MINUTES, True)
    return WaitProfile(WAIT_PROFILE_SAFE, FAILS_PER_CYCLE, MAX_CYCLES, SLEEP_MINUTES,
                       XLSX_SHEET_FORMAT_SLEEP_MINUTES, False)


def abandon_threshold(project: Project | None = None) -> int:
    """Сколько отказов шага закрывают его для проекта (9 в safe, 4 в fast)."""
    return wait_profile(project).max_total_fails


def exhausted_threshold(project: Project | None) -> int:
    """Порог, при котором шаг был закрыт: записанный при закрытии, иначе порог текущего профиля."""
    if project is not None:
        recorded = _failure_state(project).get("abandoned_threshold")
        if isinstance(recorded, int) and not isinstance(recorded, bool) and recorded > 0:
            return recorded
    return abandon_threshold(project)


def is_retry_exhausted(project: Project | None, running_key: str | None = None) -> bool:
    """Нативные повторы исчерпаны: шаг закрыт (abandoned_at) и счётчик дошёл до порога.

    ``running_key`` - счётчик именно этого running-статуса; без него берётся наибольший.
    Закрытие из-за ошибки настройки (abandoned_reason=fatal_config) сюда не относится: повтор её не лечит."""
    if project is None:
        return False
    fs = _failure_state(project)
    if not fs.get("abandoned_at") or fs.get("abandoned_reason") == "fatal_config":
        return False
    totals = fs.get("total_fails") or {}
    if running_key is not None:
        counts = [int(totals.get(running_key) or 0)]
    else:
        counts = [int(v or 0) for v in totals.values()]
    return bool(counts) and max(counts) >= exhausted_threshold(project)


def sleep_minutes_for_error(error: Exception, profile: WaitProfile | None = None) -> int:
    msg = str(error)
    long_sleep = profile.sleep_minutes if profile is not None else SLEEP_MINUTES
    quick_sleep = profile.quick_sleep_minutes if profile is not None else XLSX_SHEET_FORMAT_SLEEP_MINUTES
    if "скачанный xlsx невалиден" in msg and "листы" in msg:
        return quick_sleep
    # Неполный TSV / prose без TSV / ошибки валидации LLM-ответов — не морозить 30 мин.
    if (
        "всё ещё неполный" in msg
        or "CONTINUE_XLSX" in msg
        or "не вернула TSV" in msg
        or "project.xlsx не изменён" in msg
        or "без writeback" in msg
        or "якорь" in msg
        or "биты" in msg
        or "главное_действие" in msg
        or "покрытие" in msg
        or "один кадр на ячейку" in msg
        or "закадр" in msg
        or "apply-ops" in msg
        or "apply_ops" in msg
    ):
        return quick_sleep
    conn_markers = (
        "Cannot connect to host",
        "ClientConnectorError",
        "CDP не доступен",
        "29229",
        "connect call failed",
    )
    if any(m in msg for m in conn_markers):
        return quick_sleep
    return long_sleep


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
        logger.info(
            "[#{}] soft retry {}: без wipe, без Excel sync (DB SoT)",
            project.id,
            step_code,
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


async def _fail_active_on_both(
    session: AsyncSession,
    project: Project,
    error: Exception,
    err_code: str | None,
) -> None:
    from app.services.run_sync import mark_running_node_failed

    await mark_running_node_failed(
        session,
        project,
        error,
        initiator="worker",
        error_code=err_code,
    )
    try:
        from app.project_db import run_on_replica_project

        async def _fail(replica_session: AsyncSession, replica: Project) -> None:
            await mark_running_node_failed(
                replica_session,
                replica,
                error,
                initiator="worker",
                error_code=err_code,
            )

        await run_on_replica_project(session, project, _fail)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[#{}] fail active NodeRun on replica failed",
            project.id,
            exc_info=True,
        )


async def _push_parked_runtime(session: AsyncSession, project: Project) -> None:
    """paused/failed + leftover NodeRun → вторая БД (сайдбар и канвас одно)."""
    try:
        from app.services.run_sync import park_leftover_running_nodes

        await park_leftover_running_nodes(session, project, as_failed=True)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[#{}] park leftover NodeRuns on worker session failed",
            project.id,
            exc_info=True,
        )
    try:
        from app.project_db import sync_runtime_both_ways

        await sync_runtime_both_ways(session, project)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[#{}] parked runtime sync master↔project.db failed",
            project.id,
            exc_info=True,
        )
    try:
        from app.project_db import run_on_replica_project
        from app.services.run_sync import park_leftover_running_nodes

        async def _park(replica_session: AsyncSession, replica: Project) -> None:
            if replica.status not in (ProjectStatus.paused, ProjectStatus.failed):
                replica.status = project.status
            await park_leftover_running_nodes(
                replica_session, replica, as_failed=True
            )

        await run_on_replica_project(session, project, _park)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[#{}] park leftover NodeRuns on replica failed",
            project.id,
            exc_info=True,
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

    def _credits_exhausted() -> bool:
        if err_code_early == "gpt_credits":  # Vibecode/Kie text 402, see error_catalog
            return True
        try:
            return is_credits_failure(error) or "credits insufficient" in (err_msg_early or "").lower()
        except Exception:  # noqa: BLE001  e.g. a non-numeric provider_code
            return False

    if _credits_exhausted():
        from app.services.project_control import pause_project as pause_project_svc
        from app.services.run_sync import mark_running_node_failed

        running = project.status
        step = step_by_running_status(running)
        fs = _failure_state(project)
        fs["last_error"] = err_msg_early
        # describe_error never returns an empty code; keep a credits-specific one in the state.
        credits_code = err_code_early if err_code_early in {"gpt_credits", "media_credits"} else "gpt_credits"
        fs["last_error_code"] = credits_code
        fs["last_running"] = running.value
        fs.pop("sleep_until", None)
        _save_failure_state(project, fs)
        await mark_running_node_failed(
            session,
            project,
            error,
            initiator="worker",
            error_code=credits_code,
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
        _fs0 = _failure_state(project)
        _streak = int(_fs0.get("infra_lock_streak") or 0) + 1
        _fs0["infra_lock_streak"] = _streak
        _save_failure_state(project, _fs0)
        if _streak > INFRA_LOCK_MAX_STREAK:
            from app.services.project_control import pause_project as pause_project_svc
            # вечная блокировка БД: не крутить шаг бесконечно, пауза с причиной (человек снимает «Продолжить»)
            _fs0["infra_lock_streak"] = 0
            _save_failure_state(project, _fs0)
            await pause_project_svc(session, project)
            await session.flush()
            logger.error("[#{}] infra_db_locked {} раз подряд — пауза", project.id, _streak)
            return "pause_infra"
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
        await _fail_active_on_both(session, project, error, err_code)
        if is_running_status(project.status):
            rollback = (
                step.requires
                if step is not None and step.requires is not None
                else ProjectStatus.new
            )
            project.status = rollback
        await session.flush()
        await _push_parked_runtime(session, project)
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
    profile = wait_profile(project)
    per_cycle = profile.fails_per_cycle
    max_total = profile.max_total_fails

    totals: dict[str, int] = dict(fs.get("total_fails") or {})
    total = totals.get(key, 0) + 1
    totals[key] = total
    fs["total_fails"] = totals

    err_code, err_msg = describe_error(error)
    fs["last_error"] = err_msg
    fs["last_error_code"] = err_code
    fs["last_running"] = key
    fs["wait_profile"] = profile.name
    cycle = (total - 1) // per_cycle + 1
    fail_in_cycle = ((total - 1) % per_cycle) + 1

    await _soft_retry_without_wipe(session, project, step_code)

    fatal_config = profile.abandon_on_fatal_config and err_code in FATAL_CONFIG_ERROR_CODES
    # C-12: каждый повтор после ошибки - платный перезапуск узла в общей цепочке; успех соседнего счётчика её не сбрасывает
    from app.services import restart_chain
    chain_ok, chain_n, chain_cap = restart_chain.charge(project, running.value, "step_failure")
    chain_stop = not chain_ok and total < max_total and not fatal_config
    if total >= max_total or fatal_config or not chain_ok:
        fs["abandoned_at"] = datetime.now(timezone.utc).isoformat()
        fs["recovery_cycles"] = profile.max_cycles
        if chain_stop:
            fs["abandoned_reason"] = "restart_chain"
            fs.pop("abandoned_threshold", None)
            _m = _meta(project)
            _m["auto_paused_reason"] = restart_chain.summary(running.value, chain_n, chain_cap, "step_failure")
            _m["restart_chain_stop"] = {"node": running.value, "count": chain_n, "cap": chain_cap,
                                        "source": "step_failure"}
            project.meta = _m
        elif fatal_config and total < max_total:
            # Ошибка настройки: повтор её не вылечит, поэтому автоматическое восстановление не открывается
            # (счётчик ниже порога) и человеку сразу показывается причина.
            fs["abandoned_reason"] = "fatal_config"
            fs.pop("abandoned_threshold", None)
        else:
            fs["abandoned_reason"] = "retries_exhausted"
            fs["abandoned_threshold"] = max_total
        _save_failure_state(project, fs)
        await _fail_active_on_both(session, project, error, err_code)
        project.status = ProjectStatus.paused
        await session.flush()
        await _push_parked_runtime(session, project)
        logger.error(
            "[#{}] abandoned after {} fails (profile {}, {} cycles, reason {}) on {}",
            project.id,
            total,
            profile.name,
            profile.max_cycles,
            fs["abandoned_reason"],
            key,
        )
        return "abandon"

    if total % per_cycle == 0:
        sleep_min = sleep_minutes_for_error(error, profile)
        until = datetime.now(timezone.utc) + timedelta(minutes=sleep_min)
        fs["sleep_until"] = until.isoformat()
        fs["recovery_cycles"] = total // per_cycle
        _save_failure_state(project, fs)
        # Сначала failed по NodeRun в обеих БД (пока status ещё running-шаг).
        # state.db часто без fw_* — канвас читает project.db.
        await _fail_active_on_both(session, project, error, err_code)
        # Не откатывать в step.requires (часто enrich_N_ready): при auto_mode
        # auto_advance сразу уводит проект «назад» (например img_pr → enrich_3_ready → scripting).
        # Во время сна держим paused; ручной run_step снимает sleep.
        project.status = ProjectStatus.paused
        await session.flush()
        await _push_parked_runtime(session, project)
        logger.warning(
            "[#{}] sleep {} min after fail {}/{} on {} (cycle {}/{}, profile {}) → status={}",
            project.id,
            sleep_min,
            total,
            max_total,
            key,
            cycle,
            profile.max_cycles,
            profile.name,
            project.status.value,
        )
        return "sleep"

    _save_failure_state(project, fs)
    # Не вызываем start_step / reset_step — они удаляют файлы на диске.
    if step is not None:
        project.status = step.running_status
    short_err = err_msg.replace("\n", " ")[:120]
    retry_text = f"Повтор {fail_in_cycle} из {per_cycle}: {short_err}"
    from app.services.run_sync import (
        prepare_node_for_step_start,
        update_active_node_progress_text,
    )

    await update_active_node_progress_text(session, project, retry_text)
    await session.flush()
    try:
        from app.project_db import run_on_replica_project

        async def _retry_ui(replica_session: AsyncSession, replica: Project) -> None:
            if step is not None:
                replica.status = step.running_status
            r_meta = dict(replica.meta) if isinstance(replica.meta, dict) else {}
            r_meta["step_failure"] = dict(fs)
            replica.meta = r_meta
            try:
                await prepare_node_for_step_start(
                    replica_session,
                    replica,
                    step_code,
                    node_key=r_meta.get("active_excel_gpt_node_key"),
                    explicit_ui_start=True,
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "[#{}] soft retry: prepare replica NodeRun failed",
                    project.id,
                    exc_info=True,
                )
            await update_active_node_progress_text(
                replica_session, replica, retry_text
            )

        await run_on_replica_project(session, project, _retry_ui)
    except Exception:  # noqa: BLE001
        logger.warning(
            "[#{}] soft retry: replica NodeRun/progress sync failed",
            project.id,
            exc_info=True,
        )
    logger.warning(
        "[#{}] fail {}/{} on {} (cycle {} fail {}/{}), soft retry без wipe: {}",
        project.id,
        total,
        max_total,
        key,
        cycle,
        fail_in_cycle,
        per_cycle,
        fs["last_error"],
    )
    return "retry"


def clear_failure_on_success(project: Project, running: ProjectStatus) -> None:
    clear_chrome_recovery(project)
    fs = _failure_state(project)
    totals: dict[str, int] = dict(fs.get("total_fails") or {})
    totals.pop(running.value, None)
    fs["total_fails"] = totals
    for stale in ("abandoned_reason", "abandoned_threshold"):
        fs.pop(stale, None)
    fs.pop("sleep_until", None)
    fs.pop("recovery_cycles", None)
    fs.pop("last_error", None)
    fs.pop("infra_lock_streak", None)
    _save_failure_state(project, fs)


def _running_status_to_resume(fs: dict[str, Any]) -> ProjectStatus | None:
    """Pick a real running status: last_running, or total_fails keys (not paused)."""
    candidates: list[str] = []
    last = fs.get("last_running")
    if last:
        candidates.append(str(last))
    totals = fs.get("total_fails") or {}
    if isinstance(totals, dict):
        for key in sorted(totals, key=lambda k: int(totals.get(k) or 0), reverse=True):
            if key not in candidates:
                candidates.append(str(key))
    skip = {
        ProjectStatus.paused,
        ProjectStatus.failed,
        ProjectStatus.new,
    }
    for key in candidates:
        try:
            running = ProjectStatus(key)
        except ValueError:
            continue
        if running in skip:
            continue
        if step_by_running_status(running) is not None:
            return running
    return None


async def maybe_resume_after_sleep(
    session: AsyncSession,
    project: Project,
) -> bool:
    """После сна — снова запустить тот же шаг (auto retry)."""
    if not clear_sleep_if_expired(project):
        return False
    from app.services.gen_queue_run import is_user_stopped

    if is_user_stopped(project):
        return False
    # Error-sleep parks as paused (not generating_*). Skipping paused here
    # left projects stuck after sleep_until with the worker never seeing them.
    fs = _failure_state(project)
    running = _running_status_to_resume(fs)
    if running is None:
        return False
    step = step_by_running_status(running)
    if step is None:
        return False
    await _soft_retry_without_wipe(session, project, step.code)
    project.status = step.running_status
    await session.flush()
    logger.info(
        "[#{}] авто-резюм после паузы ошибок → {} (без wipe)",
        project.id,
        step.code,
    )
    return True


async def resume_expired_error_sleeps(session: AsyncSession) -> list[int]:
    """paused + expired sleep_until → restore last_running (worker tick)."""
    from sqlalchemy import select

    rows = (
        await session.execute(
            select(Project).where(Project.status == ProjectStatus.paused)
        )
    ).scalars().all()
    ids: list[int] = []
    for p in rows:
        if await maybe_resume_after_sleep(session, p):
            ids.append(p.id)
    return ids
