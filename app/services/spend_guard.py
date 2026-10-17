"""Один inflight на живой LLM-вызов.

Проверка и регистрация идут под одним lock, без await между ними.
Cap по умолчанию бесконечный: прод не блокируется, пока тест не сузит его.
"""

from __future__ import annotations

import asyncio
import math
import os

CALL_ESTIMATE = 1.0
_DEFAULT_PACK_LIMIT = 8


class SpendBlocked(RuntimeError):
    """Вызов не отправлен: оценка не влезает в cap."""


class _State:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.inflight = 0.0
        self.peak = 0.0
        self.cap = math.inf
        self.reservations = 0


_state = _State()


def reset() -> None:
    _state.inflight = 0.0
    _state.peak = 0.0
    _state.cap = math.inf
    _state.reservations = 0


def set_cap(cap: float) -> None:
    _state.cap = float(cap)


def inflight() -> float:
    return _state.inflight


def peak() -> float:
    return _state.peak


def reservations() -> int:
    return _state.reservations


async def reserve(estimate: float = CALL_ESTIMATE) -> None:
    """Занять оценку. Сравнение и прибавление — в одном lock, без await."""
    async with _state.lock:
        nxt = _state.inflight + float(estimate)
        if nxt > _state.cap:
            raise SpendBlocked(
                f"spend cap {_state.cap}: inflight {_state.inflight}+{estimate}"
            )
        _state.inflight = nxt
        _state.reservations += 1
        if _state.inflight > _state.peak:
            _state.peak = _state.inflight


async def release(estimate: float = CALL_ESTIMATE) -> None:
    async with _state.lock:
        _state.inflight = max(0.0, _state.inflight - float(estimate))


async def cannot_reserve(estimate: float = CALL_ESTIMATE) -> bool:
    """Повтор не влезает. Чтение inflight и cap под lock, без await между ними."""
    async with _state.lock:
        return _state.inflight + float(estimate) > _state.cap


def llm_pack_limit() -> int:
    """Общий потолок пачек. Env LLM_PACK_PARALLEL, иначе 8."""
    raw = os.environ.get("LLM_PACK_PARALLEL", "").strip()
    if not raw:
        return _DEFAULT_PACK_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_PACK_LIMIT
    return max(1, value)
