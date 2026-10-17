"""Фикстуры ядра оркестратора. БД — временный sqlite из общего conftest."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from tests.agents.mocks import MockActions, MockValidators, ScriptedRouter

TEST_TOKEN = "orch-test-token"


@pytest.fixture
def token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("ORCH_API_TOKEN", TEST_TOKEN)
    return TEST_TOKEN


@pytest.fixture
def actions() -> MockActions:
    return MockActions()


@pytest.fixture
def scripted() -> ScriptedRouter:
    return ScriptedRouter()


@pytest.fixture
def runtime(token: str, actions: MockActions, scripted: ScriptedRouter):
    del token
    rt = build_runtime(actions=actions, validators=MockValidators(), router=scripted)
    set_runtime(rt)
    yield rt
    set_runtime(None)
