"""Тесты кооперативной отмены шагов (app/services/step_cancel.py).

Покрывают:
  * request_stop / is_stop_requested / consume_stop;
  * raise_if_cancelled бросает StepCancelledError только если флаг стоял;
  * флаг идемпотентный (повторный request_stop не падает);
  * clear_all очищает все флаги.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.step_cancel import (
    StepCancelledError,
    abort_if_cancelled,
    await_with_cancel,
    clear_all,
    consume_stop,
    is_stop_requested,
    raise_if_cancelled,
    request_stop,
    sleep_cancellable,
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


def test_abort_if_cancelled_no_pid() -> None:
    abort_if_cancelled(None)


def test_abort_if_cancelled_raises_without_consume() -> None:
    request_stop(55)
    with pytest.raises(StepCancelledError):
        abort_if_cancelled(55)
    assert is_stop_requested(55) is True


@pytest.mark.asyncio
async def test_sleep_cancellable_interrupted() -> None:
    request_stop(77)
    with pytest.raises(StepCancelledError):
        await sleep_cancellable(5.0, 77, poll_s=0.05)


@pytest.mark.asyncio
async def test_await_with_cancel_interrupted() -> None:
    request_stop(88)

    async def slow() -> str:
        await asyncio.sleep(10)
        return "done"

    with pytest.raises(StepCancelledError):
        await await_with_cancel(slow(), 88, poll_s=0.05)
