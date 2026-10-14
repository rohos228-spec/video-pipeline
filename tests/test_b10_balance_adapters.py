"""B10: автоматические снимки баланса. Класс доказательства U и D: настоящий код адаптеров и автосверки,
HTTP подменён (httpx.MockTransport / patch), журналы во временных SQLite. Живых вызовов нет."""
from __future__ import annotations

import asyncio
import subprocess
import sys

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
from app.services import balance_adapters as ba, spend_guard, spend_reconcile as sr
from app.services.diagnosis import Incident
from app.settings import settings


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    sr._adapters.clear()
    ba._active.clear()
    from app.services import api_tracker_hook
    monkeypatch.setattr(api_tracker_hook, "_TRACKER_DB", tmp_path / "no_tracker.db")
    yield
    sr._adapters.clear()
    ba._active.clear()


@pytest.fixture
async def sessions(tmp_path, monkeypatch):
    from app.services import actions_v1  # noqa: F401
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    spend_guard._ensured.clear()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(spend_guard, "_sessions", lambda: factory)
    yield factory
    await engine.dispose()


def mock_httpx(monkeypatch, handler):
    real = httpx.AsyncClient

    class Client(real):
        def __init__(self, *a, **kw):
            kw.pop("proxy", None)
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "AsyncClient", Client)


# ------------------------------------------------------------------ адаптеры


async def test_kie_adapter_converts_credits_to_usd(monkeypatch):
    from app.bots import kie_http
    from app.services import kie_catalog

    async def credits():
        return 4000.0
    monkeypatch.setattr(kie_http, "get_credits", credits)
    assert await ba.kie_balance_usd() == pytest.approx(4000.0 * kie_catalog.CREDIT_USD)


async def test_kie_adapter_none_credits_is_no_snapshot(monkeypatch):
    from app.bots import kie_http

    async def credits():
        return None
    monkeypatch.setattr(kie_http, "get_credits", credits)
    ba.register_default_adapters()
    snap = await sr.take_snapshot("kie")
    assert not snap.ok and "Kie не вернул" in snap.error


async def test_outsee_adapter_over_mock_http(monkeypatch):
    monkeypatch.setattr(settings, "outsee_api_key", "test-key")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"balance_usd": 12.5})
    mock_httpx(monkeypatch, handler)
    assert await ba.outsee_balance_usd() == 12.5
    assert seen["url"].endswith("/api/v1/balance")


def test_outsee_unit_not_confirmed_is_refused():
    with pytest.raises(ba.BalanceUnavailable, match="не подтверждена"):
        ba._outsee_usd({"balance": 100})
    assert ba._outsee_usd({"balance": 7, "currency": "usd"}) == 7.0


async def test_elevenlabs_returns_chars_and_no_usd_snapshot(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "xi-test")
    seen = {}

    def handler(request):
        seen["key"] = request.headers.get("xi-api-key")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"character_count": 1000, "character_limit": 11000})
    mock_httpx(monkeypatch, handler)
    assert await ba.eleven_remaining_chars() == 10000
    assert seen["key"] == "xi-test" and seen["url"] == ba.ELEVEN_SUBSCRIPTION_URL
    assert ba.eleven_usd_per_char() is None  # цена символа пока оценка
    ba.register_default_adapters()
    snap = await sr.take_snapshot("elevenlabs")
    assert not snap.ok and "нет USD-снимка" in snap.error and "10000" in snap.error


async def test_elevenlabs_usd_when_price_is_calibrated(monkeypatch):
    from app.services import price_calibration as pc
    monkeypatch.setattr(settings, "elevenlabs_api_key", "xi-test")
    mock_httpx(monkeypatch, lambda r: httpx.Response(200, json={"character_count": 0, "character_limit": 1000}))
    cal = pc.Calibration("ELEVENLABS_USD_PER_CHAR", 0.0003, "measured", 3, "тест")
    monkeypatch.setattr(pc, "by_name", lambda: {"ELEVENLABS_USD_PER_CHAR": cal})
    assert await ba.elevenlabs_balance_usd() == pytest.approx(1000 * 0.0003)


async def test_http_error_is_not_exception_for_snapshot(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "xi-test")
    mock_httpx(monkeypatch, lambda r: httpx.Response(500, json={}))
    ba.register_default_adapters()
    snap = await sr.take_snapshot("elevenlabs")
    assert not snap.ok and "HTTP 500" in snap.error


def test_vibecode_has_no_adapter_and_is_documented():
    from app.services import price_calibration as pc
    assert "vibecode" in ba.JOURNAL_ONLY and "vibecode" not in ba.ADAPTERS
    ba.register_default_adapters()
    assert "vibecode" not in sr._adapters
    assert "ЖУРНАЛ ПРИЛОЖЕНИЯ" in pc.BALANCE_SOURCES["vibecode"]


# ------------------------------------------------------------------ без сети при импорте / регистрации


def test_no_network_on_import_or_registration():
    code = (
        "import socket\n"
        "def boom(*a, **k): raise AssertionError('network call')\n"
        "socket.socket.connect = boom\n"
        "socket.create_connection = boom\n"
        "import app.services.balance_adapters as ba\n"
        "print(ba.register_default_adapters())\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-800:]
    assert "kie" in out.stdout


# ------------------------------------------------------------------ автосверка


def test_auto_is_off_under_pytest_by_default(monkeypatch):
    monkeypatch.setattr(settings, "spend_reconcile_auto", None)
    assert ba.auto_enabled("outsee") is False
    monkeypatch.setattr(settings, "spend_reconcile_auto", True)
    assert ba.auto_enabled("outsee") is True


async def test_open_returns_none_when_disabled(monkeypatch, sessions):
    monkeypatch.setattr(settings, "spend_reconcile_auto", False)
    assert await ba.open_auto_window("generating_images", 7) is None
    assert await ba.open_auto_window("planning", 7) is None  # не платный шаг


def _balances(monkeypatch, values):
    from app.bots import outsee_http
    it = iter(values)

    async def fetch():
        v = next(it)
        if isinstance(v, Exception):
            raise v
        return {"balance_usd": v}
    monkeypatch.setattr(outsee_http, "fetch_balance", fetch)


async def _run_window(monkeypatch, sessions, values, spend_usd, project=7):
    monkeypatch.setattr(settings, "spend_reconcile_auto", True)
    monkeypatch.setattr(settings, "image_provider", "outsee")
    _balances(monkeypatch, values)
    win = await ba.open_auto_window("generating_images", project)
    assert win is not None
    if spend_usd:
        async with sessions() as s:
            await spend_guard.record_actual(s, project_id=project, usd=spend_usd, provider="outsee")
            await s.commit()
    return await ba.close_auto_window(win)


async def test_converged_writes_reconcile_ok_and_is_visible_in_telemetry(monkeypatch, sessions):
    res = await _run_window(monkeypatch, sessions, [10.0, 9.0], 1.0)
    assert res["status"] == sr.STATUS_CONVERGED and res["incident_id"] is None
    async with sessions() as s:
        rows = (await s.execute(text("SELECT source FROM spend_ledger WHERE kind='event'"))).all()
        assert [r[0] for r in rows] == [sr.SOURCE_OK]
        rep = await sr.project_report(s, 7)
    assert rep["balance_reconciled"] and rep["reconciliations_total"] == 1 and rep["reconciliations_gap"] == 0
    from app.services.telemetry import reconcile_summary
    assert "Сверок с балансом провайдера: 1" in " ".join(reconcile_summary(rep)["lines_ru"])


async def test_gap_opens_spend_mismatch_incident(monkeypatch, sessions):
    res = await _run_window(monkeypatch, sessions, [10.0, 9.0], 0.5)
    assert res["status"] == sr.STATUS_GAP and res["incident_id"]
    async with sessions() as s:
        inc = (await s.scalars(select(Incident))).all()
        rep = await sr.project_report(s, 7)
    assert [i.type for i in inc] == ["spend_mismatch"]
    assert rep["reconciliations_gap"] == 1 and rep["open_mismatch_incidents"] == 1


async def test_network_failure_never_breaks_step_only_no_snapshot_row(monkeypatch, sessions):
    res = await _run_window(monkeypatch, sessions, [10.0, ConnectionError("down")], 1.0)
    assert res["status"] == sr.STATUS_NO_SNAPSHOT
    async with sessions() as s:
        assert (await s.scalars(select(Incident))).all() == []  # без инцидента
        rep = await sr.project_report(s, 7)
    assert rep["reconciliations"][0]["status"] == sr.STATUS_NO_SNAPSHOT


async def test_before_failure_and_timeout_do_not_raise(monkeypatch, sessions):
    monkeypatch.setattr(ba, "SNAPSHOT_TIMEOUT_S", 0.05)
    from app.bots import outsee_http

    async def slow():
        await asyncio.sleep(5)
    monkeypatch.setattr(settings, "spend_reconcile_auto", True)
    monkeypatch.setattr(settings, "image_provider", "outsee")
    monkeypatch.setattr(outsee_http, "fetch_balance", slow)
    win = await ba.open_auto_window("generating_images", 7)
    assert win is not None and not win.before.ok
    res = await ba.close_auto_window(win)
    assert res["status"] == sr.STATUS_NO_SNAPSHOT


async def test_open_and_close_swallow_internal_failures(monkeypatch):
    monkeypatch.setattr(settings, "spend_reconcile_auto", True)

    def broken():
        raise RuntimeError("db down")
    monkeypatch.setattr(spend_guard, "_sessions", broken)
    assert await ba.open_auto_window("generating_images", 7) is None
    win = ba.AutoWindow("outsee", 7, None, None)
    ba._active["outsee"] = [win]
    assert await ba.close_auto_window(win) is None  # _finish упал, наружу ничего


async def test_overlapping_windows_skip_comparison(monkeypatch, sessions):
    monkeypatch.setattr(settings, "spend_reconcile_auto", True)
    monkeypatch.setattr(settings, "image_provider", "outsee")
    _balances(monkeypatch, [10.0, 10.0, 5.0, 5.0])
    w1 = await ba.open_auto_window("generating_images", 1)
    w2 = await ba.open_auto_window("generating_images", 2)
    assert w1.overlapped and w2.overlapped
    assert await ba.close_auto_window(w1) is None and await ba.close_auto_window(w2) is None
    async with sessions() as s:
        assert (await s.execute(text("SELECT COUNT(*) FROM spend_ledger"))).scalar() == 0


def test_provider_mapping(monkeypatch):
    monkeypatch.setattr(settings, "image_provider", "outsee")
    monkeypatch.setattr(settings, "video_provider", "kie")
    assert ba.provider_for_status("generating_images") == "outsee"
    assert ba.provider_for_status("generating_videos") == "kie"
    assert ba.provider_for_status("generating_music") == "kie"
    assert ba.provider_for_status("generating_audio") == "elevenlabs"
    assert ba.provider_for_status("planning") is None


def test_pipeline_is_wired():
    import inspect
    from app.orchestrator import pipeline
    src = inspect.getsource(pipeline.advance_project)
    assert "open_auto_window" in src and "close_auto_window" in src
