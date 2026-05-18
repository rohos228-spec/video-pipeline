"""Кооперативная отмена шагов.

Юзер жмёт «⏹ Остановить текущий шаг» в TG → бот вызывает `request_stop(pid)`,
чтобы пометить проект как «нужно остановить». Длинные циклы внутри шагов
(generate_images, split, generate_videos, generate_audio, assemble и т.п.)
между итерациями проверяют `is_stop_requested(pid)` и, если флаг стоит,
выходят из цикла через `raise StepCancelledError(...)` или `break`.

Это даёт «остановку между кадрами/итерациями»: текущая операция (например
картинка уже генерится в outsee) досработает до конца, а следующая итерация
не начнётся.

Старая логика `on_project_stop_running` меняла только статус в БД — но
running-task этого не видел, поэтому шаг продолжал гнаться до конца цикла
(сотни кадров). Этот модуль чинит именно эту проблему.

Браузер / Playwright / Chrome — **не трогаем**. Это исключительно про
прерывание Python-цикла шага.
"""
from __future__ import annotations

from loguru import logger


class StepCancelledError(Exception):
    """Шаг был прерван пользователем через ⏹ Остановить.

    Бросается из цикла шага, когда `is_stop_requested(pid)` стало True.
    Воркер ловит это исключение и НЕ считает его «обычной ошибкой»
    (т.е. не накручивает fail_counts и не пишет «ошибка на шаге»).
    """


_stop_pids: set[int] = set()


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
