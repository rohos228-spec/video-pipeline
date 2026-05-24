"""Отмена шагов: межпроцессный stop-файл + task.cancel() в этом процессе.

Web/API и воркер могут быть разными процессами — in-memory флаг/task не
достаточны. `request_stop` пишет `data/.stop/project_{id}.stop`, воркер
видит его в `is_stop_requested` и выходит из outsee/GPT циклов.
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable
from pathlib import Path
from typing import Any, TypeVar

from loguru import logger

from app.settings import settings

T = TypeVar("T")


class StepCancelledError(Exception):
    """Шаг был прерван пользователем через ⏹ Остановить."""


_stop_pids: set[int] = set()
_advance_tasks: dict[int, asyncio.Task] = {}
# Активная Playwright-страница outsee/GPT — закрываем при ⏹, чтобы
# прервать зависшие page.goto / expect_download / wait_for.
_active_pages: dict[int, Any] = {}


def _stop_flag_dir() -> Path:
    d = Path(settings.data_dir) / ".stop"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _stop_flag_path(project_id: int) -> Path:
    return _stop_flag_dir() / f"project_{project_id}.stop"


def _write_stop_flag(project_id: int) -> None:
    _stop_flag_path(project_id).write_text("1", encoding="utf-8")


def _clear_stop_flag(project_id: int) -> None:
    with contextlib.suppress(FileNotFoundError):
        _stop_flag_path(project_id).unlink()


def register_advance_task(project_id: int, task: asyncio.Task) -> None:
    _advance_tasks[project_id] = task


def unregister_advance_task(project_id: int) -> None:
    _advance_tasks.pop(project_id, None)


def is_advance_active(project_id: int) -> bool:
    task = _advance_tasks.get(project_id)
    return task is not None and not task.done()


def is_generation_active(project_id: int) -> bool:
    from app.services.xlsx_flow_locks import (
        XLSX_FLOW_STEP_CODES,
        is_xlsx_flow_active,
    )

    if is_advance_active(project_id):
        return True
    if any(is_xlsx_flow_active(project_id, code) for code in XLSX_FLOW_STEP_CODES):
        return True
    # stop-файл: воркер ещё не consume, но юзер уже жмёт ⏹
    return _stop_flag_path(project_id).exists()


def register_active_page(project_id: int, page: Any) -> None:
    _active_pages[project_id] = page


def unregister_active_page(project_id: int) -> None:
    _active_pages.pop(project_id, None)


async def _close_active_page(page: Any) -> None:
    with contextlib.suppress(Exception):
        await page.close()


def _interrupt_browser_for_project(project_id: int) -> None:
    page = _active_pages.get(project_id)
    if page is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_close_active_page(page))


def cancel_advance_task(project_id: int) -> bool:
    _interrupt_browser_for_project(project_id)
    task = _advance_tasks.get(project_id)
    if task is not None and not task.done():
        task.cancel()
        return True
    return False


def clear_stop(project_id: int) -> None:
    if project_id in _stop_pids:
        _stop_pids.discard(project_id)
        logger.debug("step_cancel.clear_stop: #{} флаг снят", project_id)
    _clear_stop_flag(project_id)


def request_stop(project_id: int) -> tuple[bool, list[str]]:
    """Stop: файл (все процессы) + cancel task (этот процесс)."""
    from app.services.xlsx_flow_locks import cancel_xlsx_flow_tasks

    _stop_pids.add(project_id)
    _write_stop_flag(project_id)
    cancelled_adv = cancel_advance_task(project_id)
    cancelled_xlsx = cancel_xlsx_flow_tasks(project_id)
    if cancelled_adv or cancelled_xlsx:
        logger.info(
            "step_cancel.request_stop: #{} (advance_cancel={}, xlsx_cancel={}, file=ok)",
            project_id,
            cancelled_adv,
            cancelled_xlsx,
        )
    else:
        logger.info(
            "step_cancel.request_stop: #{} stop-файл записан "
            "(воркер увидит в outsee/GPT, advance_cancel={})",
            project_id,
            cancelled_adv,
        )
    return cancelled_adv, cancelled_xlsx


def is_stop_requested(project_id: int) -> bool:
    if project_id in _stop_pids:
        return True
    if _stop_flag_path(project_id).exists():
        _stop_pids.add(project_id)
        cancel_advance_task(project_id)
        return True
    return False


def consume_stop(project_id: int) -> bool:
    was = project_id in _stop_pids or _stop_flag_path(project_id).exists()
    if was:
        _stop_pids.discard(project_id)
        _clear_stop_flag(project_id)
        logger.info("step_cancel.consume_stop: #{} флаг снят", project_id)
    return was


def abort_if_cancelled(project_id: int | None) -> None:
    if project_id is not None and is_stop_requested(project_id):
        raise StepCancelledError(
            f"проект #{project_id}: остановка по запросу пользователя"
        )


async def await_with_cancel(
    coro: Awaitable[T],
    project_id: int | None,
    *,
    poll_s: float = 0.2,
) -> T:
    if project_id is None:
        return await coro
    task = asyncio.create_task(coro)
    try:
        while not task.done():
            abort_if_cancelled(project_id)
            await asyncio.sleep(poll_s)
        return task.result()
    except StepCancelledError:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task
        raise


async def sleep_cancellable(
    seconds: float,
    project_id: int | None,
    *,
    poll_s: float = 0.2,
) -> None:
    if project_id is None:
        await asyncio.sleep(seconds)
        return
    deadline = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < deadline:
        abort_if_cancelled(project_id)
        remaining = deadline - asyncio.get_event_loop().time()
        await asyncio.sleep(min(poll_s, max(remaining, 0)))


def raise_if_cancelled(project_id: int) -> None:
    if consume_stop(project_id):
        raise StepCancelledError(
            f"проект #{project_id}: остановка по запросу пользователя"
        )


def clear_all() -> None:
    _stop_pids.clear()
    _advance_tasks.clear()
    _active_pages.clear()
    stop_dir = Path(settings.data_dir) / ".stop"
    if stop_dir.is_dir():
        for f in stop_dir.glob("project_*.stop"):
            with contextlib.suppress(OSError):
                f.unlink()
