"""Отмена шагов: флаг + немедленный `asyncio.Task.cancel()`.

Юзер жмёт «⏹ Остановить» → `request_stop(pid)`:
  1. ставит флаг (кооперативный выход из циклов);
  2. отменяет asyncio-task воркера (`advance_project_job`) и xlsx-flow.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

from loguru import logger

T = TypeVar("T")


class StepCancelledError(Exception):
    """Шаг был прерван пользователем через ⏹ Остановить.

    Бросается из цикла шага, когда `is_stop_requested(pid)` стало True.
    Воркер ловит это исключение и НЕ считает его «обычной ошибкой»
    (т.е. не накручивает fail_counts и не пишет «ошибка на шаге»).
    """


_stop_pids: set[int] = set()
_advance_tasks: dict[int, asyncio.Task] = {}


def register_advance_task(project_id: int, task: asyncio.Task) -> None:
    _advance_tasks[project_id] = task


def unregister_advance_task(project_id: int) -> None:
    _advance_tasks.pop(project_id, None)


def is_advance_active(project_id: int) -> bool:
    """True, если воркер прямо сейчас выполняет advance_project для проекта."""
    task = _advance_tasks.get(project_id)
    return task is not None and not task.done()


def is_generation_active(project_id: int) -> bool:
    """True, если для проекта идёт advance или xlsx-flow (task ещё жива)."""
    from app.services.xlsx_flow_locks import (
        XLSX_FLOW_STEP_CODES,
        is_xlsx_flow_active,
    )

    if is_advance_active(project_id):
        return True
    return any(is_xlsx_flow_active(project_id, code) for code in XLSX_FLOW_STEP_CODES)


def cancel_advance_task(project_id: int) -> bool:
    """Отменяет asyncio-task advance_project для проекта."""
    task = _advance_tasks.get(project_id)
    if task is not None and not task.done():
        task.cancel()
        return True
    return False


def clear_stop(project_id: int) -> None:
    """Снимает флаг stop без исключения (новый явный запуск шага)."""
    if project_id in _stop_pids:
        _stop_pids.discard(project_id)
        logger.debug("step_cancel.clear_stop: #{} флаг снят", project_id)


def request_stop(project_id: int) -> tuple[bool, list[str]]:
    """Помечает проект как «нужно остановить» и **сразу** отменяет task.

    Повторное нажатие ⏹ снова шлёт cancel (на случай зависшего await).
    Возвращает (advance_cancelled, xlsx_step_codes).
    """
    from app.services.xlsx_flow_locks import cancel_xlsx_flow_tasks

    _stop_pids.add(project_id)
    cancelled_adv = cancel_advance_task(project_id)
    cancelled_xlsx = cancel_xlsx_flow_tasks(project_id)
    if cancelled_adv or cancelled_xlsx:
        logger.info(
            "step_cancel.request_stop: #{} (advance_cancel={}, xlsx_cancel={})",
            project_id,
            cancelled_adv,
            cancelled_xlsx,
        )
    elif is_generation_active(project_id):
        logger.warning(
            "step_cancel.request_stop: #{} флаг установлен, но task не найден "
            "(перезапустите `python -m app.main` после обновления?)",
            project_id,
        )
    else:
        logger.info(
            "step_cancel.request_stop: #{} флаг установлен (активных task нет)",
            project_id,
        )
    return cancelled_adv, cancelled_xlsx


def is_stop_requested(project_id: int) -> bool:
    """True, если для этого проекта запрошена остановка."""
    return project_id in _stop_pids


def consume_stop(project_id: int) -> bool:
    """Атомарно проверяет флаг и снимает его, если он стоял.

    Возвращает True, если флаг был установлен (и теперь снят). Используется
    в шагах в конце цикла, чтобы корректно завершиться один раз.
    """
    if project_id in _stop_pids:
        _stop_pids.discard(project_id)
        logger.info("step_cancel.consume_stop: #{} флаг снят", project_id)
        return True
    return False


def abort_if_cancelled(project_id: int | None) -> None:
    """Проверка без снятия флага — для длинных операций внутри одной итерации."""
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
    """Ждёт coroutine, но прерывает её при ⏹ (отмена asyncio-task)."""
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
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        raise


async def sleep_cancellable(
    seconds: float,
    project_id: int | None,
    *,
    poll_s: float = 0.2,
) -> None:
    """sleep, прерываемый по флагу stop."""
    if project_id is None:
        await asyncio.sleep(seconds)
        return
    deadline = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < deadline:
        abort_if_cancelled(project_id)
        remaining = deadline - asyncio.get_event_loop().time()
        await asyncio.sleep(min(poll_s, max(remaining, 0)))


def raise_if_cancelled(project_id: int) -> None:
    """Если для проекта запрошена остановка — снимает флаг и кидает
    `StepCancelledError`. Используется внутри циклов шагов:

        for fr in frames:
            raise_if_cancelled(project.id)
            await generate(fr)
    """
    if consume_stop(project_id):
        raise StepCancelledError(
            f"проект #{project_id}: остановка по запросу пользователя"
        )


def clear_all() -> None:
    """Сбрасывает все флаги. Используется при перезапуске воркера/тестов."""
    _stop_pids.clear()
    _advance_tasks.clear()
