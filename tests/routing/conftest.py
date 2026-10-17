"""Роутер в тестах не ходит в сеть.

Класс ``socket.socket`` не подменяем: его берёт self-pipe event loop.
Закрыты ``create_connection`` и методы ``connect`` / ``connect_ex``.
Loopback пропускается: на Windows ``socketpair`` зовёт
``connect(("127.0.0.1", port))``. Гард не ловит Proactor ``ConnectEx``.
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


def _loopback(address: object) -> bool:
    if not isinstance(address, tuple) or not address:
        return False
    return str(address[0]) in {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def _routing_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def boom(*_args, **_kwargs):
        raise AssertionError("routing tests must not open TCP connections")

    def connect(self, address, *args, **kwargs):
        if _loopback(address):
            return original_connect(self, address, *args, **kwargs)
        raise AssertionError("routing tests must not open TCP connections")

    def connect_ex(self, address, *args, **kwargs):
        if _loopback(address):
            return original_connect_ex(self, address, *args, **kwargs)
        raise AssertionError("routing tests must not open TCP connections")

    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


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
