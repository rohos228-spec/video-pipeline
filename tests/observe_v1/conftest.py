from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

from app.settings import settings

_DOCKER_ONLY = {
    "test_decision_tool_cycle_gateway_ack.py",
    "test_gateway_route_policy.py",
    "test_hitl_project_authority.py",
    "test_managed_restart_safety.py",
    "test_readonly_pilot.py",
}
collect_ignore = (
    []
    if os.environ.get("VP_RUN_DOCKER_ONLY") == "1" and Path("/.dockerenv").exists()
    else [name for name in _DOCKER_ONLY if (Path(__file__).parent / name).exists()]
)


@pytest.fixture(autouse=True)
def _synthetic_loopback_credentials(monkeypatch):
    monkeypatch.setattr(settings, "gpt_api_key", "synthetic-observe-test-key")
    monkeypatch.setattr(settings, "gpt_base_url", "http://127.0.0.1:18766")


def pytest_collection_modifyitems(config, items):
    kept, dropped = [], []
    for item in items:
        cls = getattr(item, "cls", None)
        func = getattr(item, "function", None)
        if cls is not None and func is not None:
            owner = next((k for k in inspect.getmro(cls) if func.__name__ in vars(k)), cls)
            if owner is not cls and owner.__name__ == "DurableActions":
                dropped.append(item)
                continue
        kept.append(item)
    if dropped:
        config.hook.pytest_deselected(items=dropped)
        items[:] = kept
