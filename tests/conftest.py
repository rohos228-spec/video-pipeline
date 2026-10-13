"""Общие фикстуры тестов.

Центральный harness-гейт (auto_advance) в проде включён, но тесты механики
продвижения не обязаны зависеть от данных на диске — по умолчанию гейт в
тестах выключен. Тест самого гейта включает его явно (monkeypatch False).

Каждый тест получает свой ``settings.data_dir`` / ``sqlite_path`` в tmp —
иначе slug'и вроде ``vo-test``/``store`` делят /workspace/data и сыплют
соседей (FileExistsError, stale voiceover.txt).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Tests never write into the real api-tracker/tracker.db (A08 stopped silencing test-time cost rows).
os.environ.setdefault("API_TRACKER_DB", str(Path(tempfile.mkdtemp(prefix="vp-tracker-")) / "tracker.db"))

from app.settings import settings

_ORIG_DATA_DIR = Path(settings.data_dir)
_ORIG_SQLITE_PATH = Path(settings.sqlite_path)


def _clear_project_db_caches() -> None:
    from app import project_db

    project_db._PROJECT_DIR_CACHE.clear()
    project_db._FRAME_TO_PROJECT.clear()
    for cache in (project_db._ENTITY_TO_PROJECT, project_db._SCENE_TO_PROJECT, project_db._EDGE_TO_PROJECT,
                  project_db._PROMPT_TO_PROJECT, project_db._TEXT_TO_PROJECT):
        cache.clear()
    from app.services import project_db_guard

    project_db_guard.reset_state()


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


@pytest.fixture(autouse=True)
def _observer_auto_off_by_default(monkeypatch):
    """C01: automatic observation is on in production; tests keep it off so unrelated tests never write a
    journal scope into a shared master database. Tests of auto-observation enable it explicitly."""
    monkeypatch.setenv("OBSERVER_AUTO_ENABLE", "0")
    yield


@pytest.fixture(autouse=True)
def _isolated_tracker_db(tmp_path_factory, monkeypatch):
    """Each test gets its own api-tracker database: cost rows written by one test never show up in another
    (or in the real ``api-tracker/tracker.db``)."""
    try:
        from app.services import api_tracker_hook
    except Exception:
        yield
        return
    monkeypatch.setattr(api_tracker_hook, "_TRACKER_DB", tmp_path_factory.mktemp("vp-tracker") / "tracker.db")
    yield


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


@pytest.fixture(autouse=True)
def _operator_token_for_test_client(monkeypatch, request):
    """In-process clients (starlette TestClient, httpx ASGI clients) play the
    operator's own browser: they send the operator token the UI sends. Tests
    that check the guard itself carry the ``raw_client`` marker (no header)."""
    if request.node.get_closest_marker("raw_client"):
        yield
        return
    try:
        import httpx
        from starlette.testclient import TestClient

        from app.services.access_guard import OPERATOR_HEADER, get_operator_token
    except Exception:
        yield
        return

    def _wrap(original):
        def _request(self, method, url, **kwargs):
            headers = dict(kwargs.get("headers") or {})
            if not any(k.lower() == OPERATOR_HEADER.lower() for k in headers):
                headers[OPERATOR_HEADER] = get_operator_token()
            kwargs["headers"] = headers
            return original(self, method, url, **kwargs)
        return _request

    def _wrap_async(original):
        async def _request(self, method, url, **kwargs):
            headers = dict(kwargs.get("headers") or {})
            if not any(k.lower() == OPERATOR_HEADER.lower() for k in headers):
                headers[OPERATOR_HEADER] = get_operator_token()
            kwargs["headers"] = headers
            return await original(self, method, url, **kwargs)
        return _request

    monkeypatch.setattr(TestClient, "request", _wrap(TestClient.request))
    monkeypatch.setattr(httpx.AsyncClient, "request", _wrap_async(httpx.AsyncClient.request))
    yield


@pytest.fixture
def outside_tmp():
    """Пустая папка ВНЕ репозитория: общий временный каталог pytest может лежать в data/ (приложение переносит TEMP туда),
    а охрана стендов и миграций справедливо отказывается писать в боевую папку."""
    from _stand_workdir import make_workdir, remove_workdir

    path = make_workdir("t_")
    try:
        yield path
    finally:
        remove_workdir(path)
