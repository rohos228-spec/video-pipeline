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
    cancel_advance_task,
    clear_all,
    clear_stop,
    consume_stop,
    is_advance_active,
    is_stop_requested,
    raise_if_cancelled,
    register_advance_task,
    request_stop,
    sleep_cancellable,
    unregister_advance_task,
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


def test_clear_stop_without_raise() -> None:
    request_stop(33)
    clear_stop(33)
    assert is_stop_requested(33) is False


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


@pytest.mark.asyncio
async def test_is_advance_active_while_task_running() -> None:
    gate = asyncio.Event()

    async def blocked() -> None:
        gate.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(blocked())
    register_advance_task(12, task)
    await gate.wait()
    assert is_advance_active(12) is True
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    unregister_advance_task(12)
    assert is_advance_active(12) is False


@pytest.mark.asyncio
async def test_request_stop_cancels_advance_task() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_advance() -> None:
        started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(long_advance())
    register_advance_task(99, task)
    await started.wait()
    adv, xlsx = request_stop(99)
    assert adv is True
    assert xlsx == []
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()
    assert cancel_advance_task(99) is False
    unregister_advance_task(99)
