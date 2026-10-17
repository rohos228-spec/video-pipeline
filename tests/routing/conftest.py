"""Роутер в тестах не ходит в сеть.

Класс ``socket.socket`` не подменяем: его берёт self-pipe event loop.
Закрыты ``create_connection`` и методы ``connect`` / ``connect_ex``:
async httpx идёт через ``sock.connect``, не через ``create_connection``.
"""

from __future__ import annotations

import socket

import pytest

from app.orchestrator.routing.config import load_config
from app.orchestrator.routing.ledger import SpendLedger, reset_spend_ledger
from app.orchestrator.routing.sink import InMemoryLlmCallSink


class RecordingSleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(float(seconds))


@pytest.fixture(autouse=True)
def _fresh_process_ledger() -> None:
    reset_spend_ledger()


@pytest.fixture(autouse=True)
def _routing_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("routing tests must not open TCP connections")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket.socket, "connect_ex", boom)


@pytest.fixture
def sleeper() -> RecordingSleeper:
    return RecordingSleeper()


@pytest.fixture
def config():
    return load_config(apply_env=False)


@pytest.fixture
def sink() -> InMemoryLlmCallSink:
    return InMemoryLlmCallSink()


@pytest.fixture
def ledger() -> SpendLedger:
    return SpendLedger(internal_budget_usd=10.0)
