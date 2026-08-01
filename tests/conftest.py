"""Общие фикстуры тестов.

Центральный harness-гейт (auto_advance) в проде включён, но тесты механики
продвижения не обязаны зависеть от данных на диске — по умолчанию гейт в
тестах выключен. Тест самого гейта включает его явно (monkeypatch False).

Каждый тест получает свой ``settings.data_dir`` / ``sqlite_path`` в tmp —
иначе slug'и вроде ``vo-test``/``store`` делят /workspace/data и сыплют
соседей (FileExistsError, stale voiceover.txt).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.settings import settings

_ORIG_DATA_DIR = Path(settings.data_dir)
_ORIG_SQLITE_PATH = Path(settings.sqlite_path)


@pytest.fixture(autouse=True)
def _isolate_settings_paths(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(settings, "harness_gate_disabled", True)
    root = tmp_path_factory.mktemp("vp-isol")
    monkeypatch.setattr(settings, "data_dir", root)
    monkeypatch.setattr(settings, "sqlite_path", root / "state.db")
    yield
    # На случай прямой записи settings.data_dir = ... в обход monkeypatch.
    settings.data_dir = _ORIG_DATA_DIR
    settings.sqlite_path = _ORIG_SQLITE_PATH
