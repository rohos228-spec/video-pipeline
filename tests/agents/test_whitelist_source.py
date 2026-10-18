"""Белый список: whitelist(), иначе metadata_registry(), иначе прежний путь."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.wiring import ActionsBridge
from app.orchestrator.contracts import not_in_whitelist


class _Card:
    """Как адаптер WS2: metadata_of на любое имя отдаёт карточку, не KeyError."""

    def metadata_of(self, name: str) -> dict[str, object]:
        return {
            "name": name,
            "risk": "high",
            "read_only": False,
            "destructive": True,
            "paid": True,
        }

    async def execute(self, name: str, params: dict, task_ctx: dict) -> dict:
        del params, task_ctx
        self.calls.append(name)
        return {"ok": True, "data": {}, "change_id": None, "cost": 0.0}


class _WhitelistOnly(_Card):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def whitelist(self) -> list[str]:
        return ["look", "bash", "create_node@new_group"]

    def metadata_registry(self) -> dict[str, dict[str, object]]:
        return {
            "look": {"name": "look"},
            "frobnicate": {"name": "frobnicate"},
        }


class _EmptyWhitelist(_Card):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def whitelist(self) -> list[str]:
        return []


class _BrokenWhitelist(_Card):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def whitelist(self) -> list[str]:
        raise RuntimeError("реестр недоступен")


def test_whitelist_is_preferred_and_still_filters_pattern() -> None:
    registry = _WhitelistOnly()
    assert not_in_whitelist("frobnicate", registry) is True
    assert not_in_whitelist("look", registry) is False
    assert not_in_whitelist("bash", registry) is True
    assert not_in_whitelist("create_node@new_group", registry) is True
    assert not_in_whitelist("look", _EmptyWhitelist()) is True
    assert not_in_whitelist("look", _BrokenWhitelist()) is True


@pytest.mark.asyncio
async def test_bridge_does_not_call_unlisted_whitelist_name() -> None:
    registry = _WhitelistOnly()
    await ActionsBridge(registry).execute("frobnicate", {}, {})
    assert registry.calls == []
