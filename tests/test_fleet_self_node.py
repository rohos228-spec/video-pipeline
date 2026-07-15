"""Fleet node identity helpers."""

from __future__ import annotations

import pytest

from app.fleet.self_node import is_local_fleet_node
from app.models import FleetNode


def test_is_local_fleet_node_by_name_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.fleet.self_node.settings.fleet_node_name", "main-pc")

    self_node = FleetNode(
        id=1,
        name="main-pc",
        base_url="http://100.72.202.35:8765",
        is_main=True,
        role="hub",
    )
    foreign_same_localhost = FleetNode(
        id=2,
        name="child-pc",
        base_url="http://127.0.0.1:8765",
        is_main=False,
        role="agent",
    )
    foreign_wrong_localhost = FleetNode(
        id=3,
        name="child-pc",
        base_url="http://127.0.0.1:8765",
        is_main=True,
        role="agent",
    )

    assert is_local_fleet_node(self_node) is True
    assert is_local_fleet_node(foreign_same_localhost) is False
    assert is_local_fleet_node(foreign_wrong_localhost) is False
