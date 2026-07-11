"""Ответ на ошибки Chrome/CDP: перезапуск браузера, ретрай шага, не 9× abandon.

Политика:
  1. Ошибка CDP / мёртвый браузер / connect_over_cdp hang
  2. → перезапуск Chrome (Windows: VpBrowserProfile.ps1, Linux: google-chrome)
  3. → soft retry того же шага (без wipe, без +1 к total_fails)
  4. После MAX_CHROME_RESTARTS неудачных перезапусков → paused + понятное сообщение
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.chrome_cdp import (
    ChromeCdpUnavailableError,
    ensure_cdp_ready,
    is_cdp_connection_error,
    playwright_cdp_hang,
    restart_chrome_cdp,
)
from app.models import Project, ProjectStatus
from app.telegram.menu import step_by_running_status

MAX_CHROME_RESTARTS_PER_STEP = 5
CHROME_RETRY_SLEEP_SEC = 3.0

_DEAD_BROWSER_MARKERS = (
    "target page, context or browser has been closed",
    "target closed",
    "browser has been closed",
    "context has been closed",
    "browser closed",
    "connection closed",
    "websocket: close",
)


def is_chrome_infra_error(exc: BaseException) -> bool:
    """Ошибка инфраструктуры Chrome — лечится перезапуском, не abandon 9×."""
    if isinstance(exc, ChromeCdpUnavailableError):
        return True
    if is_cdp_connection_error(exc):
        return True
    if playwright_cdp_hang(exc):
        return True
    msg = str(exc).lower()
    if "connect_over_cdp" in msg and "timeout" in msg:
        return True
    return any(m in msg for m in _DEAD_BROWSER_MARKERS)


def _chrome_recovery_state(project: Project) -> dict[str, Any]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw = meta.get("chrome_recovery")
    return dict(raw) if isinstance(raw, dict) else {}


def _save_chrome_recovery(project: Project, state: dict[str, Any]) -> None:
    meta = dict(project.meta or {})
    meta["chrome_recovery"] = state
    project.meta = meta


def clear_chrome_recovery(project: Project) -> None:
    meta = dict(project.meta or {})
    if "chrome_recovery" in meta:
        del meta["chrome_recovery"]
        project.meta = meta


async def restart_chrome_for_pipeline(*, reason: str) -> bool:
    """Перезапустить Chrome и дождаться CDP. True = готов к работе."""
    logger.warning("chrome_recovery: перезапуск Chrome ({})", reason[:200])
    ok = await restart_chrome_cdp(reason=reason)
    if not ok:
        await asyncio.sleep(CHROME_RETRY_SLEEP_SEC)
        ok = await restart_chrome_cdp(reason=f"повтор: {reason[:120]}")
    if ok:
        try:
            await ensure_cdp_ready(force_recover=False)
        except ChromeCdpUnavailableError:
            return False
    return ok


async def handle_chrome_step_failure(
    session: AsyncSession,
    project: Project,
    error: Exception,
) -> str:
    """Обработать Chrome/CDP ошибку на шаге.

    Returns:
        retry — Chrome перезапущен (или будет ещё попытка), шаг повторить
        pause_infra — исчерпаны перезапуски, paused
    """
    from app.services.step_failure_policy import _soft_retry_without_wipe

    running = project.status
    step = step_by_running_status(running)
    step_code = step.code if step else running.value
    key = running.value

    cr = _chrome_recovery_state(project)
    attempts = int(cr.get("restart_attempts") or 0) + 1
    cr["restart_attempts"] = attempts
    cr["last_error"] = f"{type(error).__name__}: {error}"
    cr["last_at"] = datetime.now(timezone.utc).isoformat()
    _save_chrome_recovery(project, cr)

    logger.warning(
        "[#{}] Chrome/CDP сбой {}/{} на {}: {}",
        project.id,
        attempts,
        MAX_CHROME_RESTARTS_PER_STEP,
        key,
        error,
    )

    if attempts <= MAX_CHROME_RESTARTS_PER_STEP:
        recovered = await restart_chrome_for_pipeline(reason=str(error))
        cr["last_restart_ok"] = recovered
        cr["last_restart_at"] = datetime.now(timezone.utc).isoformat()
        _save_chrome_recovery(project, cr)
        await session.flush()

        if recovered:
            logger.info(
                "[#{}] Chrome перезапущен — retry {} без +1 fail",
                project.id,
                step_code,
            )
        else:
            logger.warning(
                "[#{}] перезапуск Chrome не помог (попытка {}) — retry через {} с",
                project.id,
                attempts,
                CHROME_RETRY_SLEEP_SEC,
            )
            await asyncio.sleep(CHROME_RETRY_SLEEP_SEC)

        await _soft_retry_without_wipe(session, project, step_code)
        if step is not None:
            project.status = step.running_status
        await session.flush()
        return "retry"

    # Исчерпаны перезапуски — paused, не 9× abandon
    fs_meta = dict(project.meta or {})
    step_fs = dict(fs_meta.get("step_failure") or {})
    step_fs["last_error"] = (
        f"Chrome не восстановился после {MAX_CHROME_RESTARTS_PER_STEP} перезапусков. "
        f"Запустите Start-Chrome.cmd, войдите в ChatGPT, нажмите ▶. "
        f"Последняя ошибка: {error}"
    )
    step_fs["infra_pause"] = "chrome_cdp"
    fs_meta["step_failure"] = step_fs
    project.meta = fs_meta
    project.status = ProjectStatus.paused
    await session.flush()
    logger.error(
        "[#{}] paused: Chrome не восстановился после {} перезапусков",
        project.id,
        MAX_CHROME_RESTARTS_PER_STEP,
    )
    return "pause_infra"
