"""Пути от корня репозитория не зависят от CWD (например web/ после npm build)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.project_root import find_project_root, resolve_project_path


def test_find_project_root_has_pyproject() -> None:
    root = find_project_root()
    assert (root / "pyproject.toml").is_file()


def test_resolve_project_path_from_subdirectory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = find_project_root()
    sub = root / "web"
    sub.mkdir(exist_ok=True)
    monkeypatch.chdir(sub)
    resolved = resolve_project_path(Path("./data/state.db"))
    assert resolved == root / "data" / "state.db"


def test_settings_db_not_under_web(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = find_project_root()
    sub = root / "web"
    sub.mkdir(exist_ok=True)
    monkeypatch.chdir(sub)
    # Перезагрузка settings после смены CWD. Объект settings возвращаем:
    # reload подменяет синглтон, и следующие тесты видят не тот sqlite_path,
    # который изолирует conftest.
    import importlib

    import app.settings as settings_mod

    previous = settings_mod.settings
    try:
        importlib.reload(settings_mod)
        db = settings_mod.settings.db_url
        assert "/web/data/" not in db.replace("\\", "/")
        assert "state.db" in db
    finally:
        settings_mod.settings = previous
