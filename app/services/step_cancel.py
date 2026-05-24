"""Кооперативная отмена шагов.

Юзер жмёт «⏹ Остановить» → `request_stop(pid)`. Циклы шагов и все
ожидания outsee (`_first_visible`, `_wait_video_url`, page.goto) проверяют
флаг каждые ~200–300 мс и сразу бросают `StepCancelledError` — текущая
итерация не дожидается таймаута Playwright.
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


def clear_stop(project_id: int) -> None:
    """Снимает флаг stop без исключения (новый явный запуск шага)."""
    if project_id in _stop_pids:
        _stop_pids.discard(project_id)
        logger.debug("step_cancel.clear_stop: #{} флаг снят", project_id)


def request_stop(project_id: int) -> None:
    """Помечает проект как «нужно остановить».

    Идемпотентно: повторные вызовы — no-op. Флаг будет снят на следующей
    итерации цикла шага через `consume_stop`.
    """
    if project_id in _stop_pids:
        logger.debug("step_cancel.request_stop: #{} уже помечен", project_id)
        return
    _stop_pids.add(project_id)
    logger.info("step_cancel.request_stop: #{} помечен для остановки", project_id)


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
