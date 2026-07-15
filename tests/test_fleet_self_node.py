"""Fleet node identity helpers."""

from __future__ import annotations

import pytest

from app.fleet.self_node import is_local_fleet_node
from app.models import FleetNode


def test_is_local_fleet_node_by_name_not_foreign_is_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.fleet.self_node.settings.fleet_node_name", "main-pc")
    monkeypatch.setattr(
        "app.fleet.self_node.settings.fleet_public_url", "http://100.72.202.35:8765"
    )
    monkeypatch.setattr("app.fleet.self_node.settings.fleet_is_main", True)

    self_node = FleetNode(
        id=1,
        name="main-pc",
        base_url="http://100.72.202.35:8765",
        is_main=True,
        role="hub",
    )
    foreign_main = FleetNode(
        id=2,
        name="child-pc",
        base_url="http://100.100.240.106:8765",
        is_main=True,
        role="agent",
    )

    assert is_local_fleet_node(self_node) is True
    assert is_local_fleet_node(foreign_main) is False
