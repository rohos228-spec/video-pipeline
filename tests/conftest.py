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
import re
import sys
import tempfile
from pathlib import Path

import pytest

# Tests never write into the real api-tracker/tracker.db (A08 stopped silencing test-time cost rows).
os.environ.setdefault("API_TRACKER_DB", str(Path(tempfile.mkdtemp(prefix="vp-tracker-")) / "tracker.db"))

# Системный прокси Windows (VPN-клиент в режиме system proxy) не должен перехватывать loopback-стенды тестов:
# urllib/httpx читают его из реестра, и фейк-серверы 127.0.0.1 отвечали «Remote end closed connection». Берём прокси только из env.
import urllib.request as _urllib_request  # noqa: E402

_urllib_request.getproxies = _urllib_request.getproxies_environment
_urllib_request.proxy_bypass = _urllib_request.proxy_bypass_environment

# Соседние служебные модули tests/ (_pytest_guard, _stand_workdir) должны находиться и без PYTHONPATH=tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _pytest_guard  # noqa: E402

# Глобальный движок БД создаётся при импорте app.db и берёт путь из SQLITE_PATH. Без переменной это боевой data/state.db
# (один тест уже записал в него проект), поэтому ДО импорта приложения ставим временные SQLITE_PATH / DATA_DIR,
# а явный путь внутрь боевой data/ отклоняем.
_SKIPPED: list[tuple[str, str]] = []  # (nodeid, причина) пропущенных тестов этого прогона
_LIVE_DB_ERROR = _pytest_guard.prepare_isolated_env()
if _LIVE_DB_ERROR:
    raise pytest.UsageError(_LIVE_DB_ERROR)

from app.settings import settings

_ORIG_DATA_DIR = Path(settings.data_dir)
_ORIG_SQLITE_PATH = Path(settings.sqlite_path)


def pytest_configure(config):
    """Один процесс pytest за раз: настоящий замок вместо договорённости (tests/_pytest_guard.py)."""
    if hasattr(config, "workerinput") or config.getoption("collectonly", default=False):
        return
    if not _pytest_guard.lock_wanted():
        return
    ok, message = _pytest_guard.acquire_run_lock(wait_s=float(os.environ.get("VP_PYTEST_LOCK_WAIT") or 0))
    if not ok:
        raise pytest.UsageError(message)
    os.environ[_pytest_guard.HELD_ENV] = str(os.getpid())
    config._vp_run_lock = True


def pytest_unconfigure(config):
    if getattr(config, "_vp_run_lock", False):
        _pytest_guard.release_run_lock()
        os.environ.pop(_pytest_guard.HELD_ENV, None)


_FFMPEG_RE = re.compile(r"ffmpeg|ffprobe", re.IGNORECASE)


def _skipif_reason(mark) -> str:
    reason = mark.kwargs.get("reason")
    if reason is None and len(mark.args) > 1:
        reason = mark.args[1]
    return str(reason or "")


def pytest_collection_modifyitems(config, items):
    """Q-15: тесты, которые молча пропускаются без ffmpeg/ffprobe, получают явную метку ``ffmpeg``
    (``-m ffmpeg`` выбирает именно их, ``-m "not ffmpeg"`` их исключает)."""
    for item in items:
        for mark in item.iter_markers("skipif"):
            if _FFMPEG_RE.search(_skipif_reason(mark)):
                item.add_marker(pytest.mark.ffmpeg)
                break


def pytest_runtest_logreport(report):
    if report.skipped and report.when in ("setup", "call"):
        longrepr = report.longrepr
        reason = str(longrepr[2]) if isinstance(longrepr, tuple) and len(longrepr) == 3 else str(longrepr)
        _SKIPPED.append((report.nodeid, reason))


def pytest_terminal_summary(terminalreporter):
    """Пропуски без ffmpeg видны в итоге прогона; при VP_STRICT_SKIPS=1 (CI) они делают прогон красным."""
    missing = [(n, r) for n, r in _SKIPPED if _FFMPEG_RE.search(r)]
    if not missing:
        return
    terminalreporter.section("пропуски из-за ffmpeg/ffprobe")
    terminalreporter.write_line(
        f"Пропущено без ffmpeg/ffprobe: {len(missing)} тест(ов) в {len({n.split('::')[0] for n, _ in missing})} файл(ах). "
        "Проверки медиа в этот прогон не вошли; поставьте ffmpeg и ffprobe, чтобы они выполнились."
    )
    if os.environ.get("VP_STRICT_SKIPS") == "1":
        terminalreporter.write_line("VP_STRICT_SKIPS=1: пропуск из-за отсутствия ffmpeg считается ошибкой прогона.", red=True)


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("VP_STRICT_SKIPS") == "1" and exitstatus == 0 and any(_FFMPEG_RE.search(r) for _, r in _SKIPPED):
        session.exitstatus = 1


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

    violation = _pytest_guard.live_db_violation(engine.url.database)
    if violation:
        pytest.exit(violation, returncode=3)

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
