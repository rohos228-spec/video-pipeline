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


def _clear_project_db_caches() -> None:
    from app import project_db

    project_db._PROJECT_DIR_CACHE.clear()
    project_db._FRAME_TO_PROJECT.clear()


@pytest.fixture(autouse=True)
def _isolate_settings_paths(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(settings, "harness_gate_disabled", True)
    root = tmp_path_factory.mktemp("vp-isol")
    monkeypatch.setattr(settings, "data_dir", root)
    monkeypatch.setattr(settings, "sqlite_path", root / "state.db")
    # Кэши project_db живут в модуле: чужой project.db из tmp предыдущего теста
    # уводит запись в изолированную БД соседа (см. startup_guard).
    _clear_project_db_caches()
    yield
    _clear_project_db_caches()
    # На случай прямой записи settings.data_dir = ... в обход monkeypatch.
    settings.data_dir = _ORIG_DATA_DIR
    settings.sqlite_path = _ORIG_SQLITE_PATH


@pytest.fixture
def fake_provider_keys(monkeypatch):
    """Фиктивные (не настоящие) ключи/базы провайдеров для тестов с mock-транспортом.

    Тесты ходят в мокнутые клиенты, но гейты ``require_gpt_api`` / ``_headers`` /
    ``kie_auth_headers`` проверяют лишь НАЛИЧИЕ ключа. На dev-машине его даёт
    локальный ``.env``; в чистом чекауте (CI, чужой пользователь, свежий клон)
    ключей нет и тесты падают с ``no_key``. Реальные запросы с этими значениями
    невозможны: хосты ``*.invalid`` не резолвятся.
    Существующие значения не перетираются.
    """
    import os

    def _fill(attr: str, value: str) -> None:
        if not str(getattr(settings, attr, "") or "").strip():
            monkeypatch.setattr(settings, attr, value)

    _fill("gpt_api_key", "test-fake-gpt-key")
    _fill("gpt_base_url", "https://gpt.invalid")
    _fill("outsee_api_key", "test-fake-outsee-key")
    if not os.environ.get("KIE_API_KEY"):
        monkeypatch.setenv("KIE_API_KEY", "test-fake-kie-key")
    try:
        from app.bots import kie_kling

        kie_kling.reset_working_kie_key()
    except Exception:
        pass
    yield
    try:
        from app.bots import kie_kling

        kie_kling.reset_working_kie_key()
    except Exception:
        pass


@pytest.fixture(scope="session", autouse=True)
def _ensure_base_schema():
    import asyncio

    from app.db import engine
    from app.main import _init_db

    async def _run() -> None:
        await _init_db()
        await engine.dispose()

    asyncio.run(_run())
