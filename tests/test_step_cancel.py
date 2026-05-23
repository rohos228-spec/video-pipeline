"""Тесты кооперативной отмены шагов (app/services/step_cancel.py).

Покрывают:
  * request_stop / is_stop_requested / consume_stop;
  * raise_if_cancelled бросает StepCancelledError только если флаг стоял;
  * флаг идемпотентный (повторный request_stop не падает);
  * clear_all очищает все флаги.
"""
from __future__ import annotations

import pytest

from app.services.step_cancel import (
    StepCancelledError,
    clear_all,
    consume_stop,
    is_stop_requested,
    raise_if_cancelled,
    request_stop,
)


@pytest.fixture(autouse=True)
def _isolate_flags():
    clear_all()
    yield
    clear_all()


def test_is_stop_requested_false_by_default() -> None:
    assert is_stop_requested(1) is False
    assert is_stop_requested(999) is False


def test_request_stop_marks_pid() -> None:
    request_stop(42)
    assert is_stop_requested(42) is True
    assert is_stop_requested(43) is False


def test_request_stop_idempotent() -> None:
    request_stop(7)
    request_stop(7)
    request_stop(7)
    assert is_stop_requested(7) is True


def test_consume_stop_clears_flag() -> None:
    request_stop(100)
    assert consume_stop(100) is True
    assert is_stop_requested(100) is False
    # повторный consume — False (флаг уже снят).
    assert consume_stop(100) is False


def test_consume_stop_not_set() -> None:
    assert consume_stop(500) is False


def test_raise_if_cancelled_no_flag_silent() -> None:
    # Не должен бросать, если флаг не стоял.
    raise_if_cancelled(11)
    raise_if_cancelled(11)


def test_raise_if_cancelled_with_flag_raises_and_clears() -> None:
    request_stop(22)
    with pytest.raises(StepCancelledError):
        raise_if_cancelled(22)
    # после raise флаг должен быть снят (один раз только бросает).
    assert is_stop_requested(22) is False
    raise_if_cancelled(22)  # уже не бросает


def test_multiple_pids_independent() -> None:
    request_stop(1)
    request_stop(2)
    assert is_stop_requested(1) is True
    assert is_stop_requested(2) is True
    assert is_stop_requested(3) is False
    consume_stop(1)
    assert is_stop_requested(1) is False
    assert is_stop_requested(2) is True


def test_clear_all() -> None:
    request_stop(1)
    request_stop(2)
    request_stop(3)
    clear_all()
    assert is_stop_requested(1) is False
    assert is_stop_requested(2) is False
    assert is_stop_requested(3) is False


def test_consume_stop_after_natural_step_completion_prevents_orphan_flag() -> None:
    """Регрессия: если юзер нажал ⏹, но шаг завершился штатно (последняя
    итерация цикла завершилась до проверки raise_if_cancelled), флаг
    остаётся в _stop_pids («orphan flag»). Воркер должен вызывать
    consume_stop(pid) после штатного возврата из advance_project, чтобы
    дренировать этот остаток.

    Тест имитирует сценарий: шаг завершился без StepCancelledError, но
    флаг был выставлен — consume_stop снимает его, следующий шаг не
    пострадает.
    """
    pid = 99

    # Флаг выставлен (Stop-запрос пришёл в «мёртвой зоне» — между последней
    # итерацией и выходом из шага).
    request_stop(pid)
    assert is_stop_requested(pid) is True

    # Воркер вызывает consume_stop после штатного завершения шага.
    consumed = consume_stop(pid)

    assert consumed is True, "consume_stop должен вернуть True (флаг был)"
    assert is_stop_requested(pid) is False, (
        "флаг должен быть снят — следующий шаг не получит паразитный StepCancelledError"
    )

    # Второй вызов — идемпотентен, флаг уже снят.
    assert consume_stop(pid) is False
