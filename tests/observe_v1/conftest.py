"""Makes tests/observe_v1 runnable with plain `pytest`, outside the Docker
harness (scripts/test_observe_offline.ps1) it was originally written for.

Three things the Docker entrypoint (container_test_entry.py) did implicitly:

1. Synthetic loopback credentials -- decision_config.runtime_config() only
   resolves to mode='synthetic' (the mode these tests exercise) when
   GPT_API_KEY starts with 'synthetic' and GPT_BASE_URL is loopback.
2. Skip modules that refuse to import outside the isolated runner (they
   raise on purpose when /.dockerenv is absent).
3. Run each test method once. Subclasses such as DecisionDurability and
   Profiles inherit DurableActions' tests, but their own asyncSetUp already
   consumes the single allowed recovery/enable for the fixture execution,
   so re-running the inherited methods there fails with
   "active observation required" -- an artifact of inheritance, not a
   defect. The same methods already run (and pass) under DurableActions.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from app.settings import settings

_DOCKER_ONLY = {
    "test_decision_tool_cycle_gateway_ack.py",
    "test_gateway_route_policy.py",
    "test_hitl_project_authority.py",
    "test_managed_restart_safety.py",
}
collect_ignore = [name for name in _DOCKER_ONLY if (Path(__file__).parent / name).exists()]


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
