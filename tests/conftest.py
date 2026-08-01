"""Общие фикстуры тестов.

Центральный harness-гейт (auto_advance) в проде включён, но тесты механики
продвижения не обязаны зависеть от данных на диске — по умолчанию гейт в
тестах выключен. Тест самого гейта включает его явно (monkeypatch False).
"""

from __future__ import annotations

import pytest

from app.settings import settings


@pytest.fixture(autouse=True)
def _harness_gate_off_in_tests(monkeypatch):
    monkeypatch.setattr(settings, "harness_gate_disabled", True)
    yield
