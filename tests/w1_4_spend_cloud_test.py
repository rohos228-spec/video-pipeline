"""W1-4 (A08 + A17): запись трат без молчаливых потерь; облачный трекер выключен по умолчанию.

Класс доказательства: U/D на заменителе провайдера и сети. Живых прогонов нет: ни сети, ни платных вызовов,
ни боевой базы. Тестовая БД лежит в tmp_path, трекер пишет в tmp_path.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services import api_tracker_hook as hook
from app.services import media_spend, outsee_guard, spend_guard
from app.services.observation import _cost_project, _track_api_cost
from app.settings import Settings, settings

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clean_gap_state():
    media_spend.reset_gap_state()
    yield
    media_spend.reset_gap_state()


@pytest.fixture
def log_lines():
    lines: list[str] = []
    hid = logger.add(lambda m: lines.append(str(m)), level="WARNING", format="{level} {message}")
    yield lines
    logger.remove(hid)


@pytest_asyncio.fixture
async def ledger(tmp_path, monkeypatch):
    """Настоящий spend_guard на временной SQLite; media_spend пишет в неё через app.db.SessionLocal."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ledger.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr("app.db.SessionLocal", sessions)
    monkeypatch.setattr(media_spend, "TEST_FORCE", True)  # эта БД своя: проверку «целевая ли БД» пропускаем
    monkeypatch.setattr(settings, "elevenlabs_api_key", "real-looking-key")
    token = _cost_project.set(7)
    yield sessions
    _cost_project.reset(token)
    await engine.dispose()


async def _rows(sessions):
    await media_spend.drain()
    async with sessions() as s:
        await spend_guard.ensure_schema(s)
        return (await s.scalars(select(spend_guard.SpendLedger).order_by(spend_guard.SpendLedger.id))).all()


def _gap_file_rows() -> list[dict]:
    path = Path(settings.data_dir) / "spend_gaps.jsonl"
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------- A08: потери записи видны

async def test_failed_write_leaves_a_gap_row_with_the_lost_amount(ledger, log_lines):
    with patch.object(spend_guard, "_valid", side_effect=RuntimeError("db locked")):
        media_spend.record("elevenlabs", usd=0.42, note="tts chars=10")
        rows = await _rows(ledger)
    gaps = [r for r in rows if r.source == "write_failed"]
    assert len(gaps) == 1 and gaps[0].kind == "event"
    assert "usd=0.420000" in gaps[0].note and "RuntimeError" in gaps[0].note
    assert gaps[0].project_id == 7 and gaps[0].provider == "elevenlabs"
    assert media_spend.gap_count() == 1
    assert any("record LOST" in ln for ln in log_lines)  # не молча: есть строка лога с трассировкой


async def test_unreachable_database_spills_to_gap_file_and_counts(tmp_path, monkeypatch, log_lines):
    monkeypatch.setattr(media_spend, "TEST_FORCE", True)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "real-looking-key")

    def broken():
        raise RuntimeError("db down")

    with patch("app.db.SessionLocal", broken):
        media_spend.record("elevenlabs", usd=1.5, note="tts")
        await media_spend.drain()
    spilled = _gap_file_rows()
    assert len(spilled) == 1 and spilled[0]["usd"] == 1.5 and spilled[0]["what"] == "spend_row"
    assert media_spend.gap_count() == 1
    assert any("record LOST" in ln for ln in log_lines)


async def test_credit_stop_and_task_row_failures_are_traced(ledger):
    with patch.object(spend_guard, "engage_on_credit_error", side_effect=RuntimeError("x")):
        await media_spend._credit_stop(7, "kie", "kie 402: n")
    with patch.object(spend_guard, "append_task_event", side_effect=RuntimeError("y")):
        await media_spend._write_task(project_id=7, key="k", task_id="T1", provider="kie", api="jobs",
                                      model="m", event="submitted", status="submitted", est_usd=0.1)
    rows = await _rows(ledger)
    whats = sorted(r.note.split()[1] for r in rows if r.source == "write_failed")
    assert whats == ["credit_stop_402", "provider_task"]
    assert media_spend.gap_count() == 2


def test_spawn_without_a_loop_is_traced_not_swallowed(log_lines):
    async def never():
        return None

    media_spend._spawn(never())  # нет работающего цикла: раньше coro.close() молча
    assert media_spend.gap_count() == 1
    assert any(r["what"] == "spawn" for r in _gap_file_rows())
    assert any("record LOST" in ln for ln in log_lines)


async def test_record_internal_error_is_traced(ledger):
    with patch.object(media_spend, "_spawn", side_effect=RuntimeError("sched")):
        media_spend.record("elevenlabs", usd=0.1, note="n")
    assert media_spend.gap_count() == 1
    assert [r["what"] for r in media_spend.recent_gaps()] == ["record"]


# ---------------------------------------------------------------- A08: оценка и факт раздельно

async def test_estimate_and_fact_are_separate_kinds(ledger):
    media_spend.record("elevenlabs", usd=0.3, note="tts est")                   # посчитанная цена
    media_spend.record("elevenlabs", usd=0.0, note="rejected", unknown=False)    # отказ: списания не было
    media_spend.record("elevenlabs", usd=0.0, note="img", unknown=True)          # цена неизвестна
    rows = [r for r in await _rows(ledger) if r.source == "media_native"]
    by_note = {r.note.split(" cost_unknown")[0].split(" synthetic")[0]: r for r in rows}
    assert by_note["tts est"].kind == "estimated" and by_note["tts est"].usd == pytest.approx(0.3)
    assert by_note["rejected"].kind == "actual" and by_note["rejected"].usd == 0.0
    assert by_note["img"].kind == "estimated" and "cost_unknown=true" in [r.note for r in rows if r.kind == "estimated"][-1]


async def test_provider_reported_amount_is_a_settle_row_and_totals_still_sum(ledger):
    media_spend.record("elevenlabs", usd=0.30, note="est")
    media_spend._spawn(media_spend._write(7, "elevenlabs", 0.21, "", settle_from=0.30))
    rows = await _rows(ledger)
    assert [r.kind for r in rows] == ["estimated", "settle"]
    async with ledger() as s:
        assert await spend_guard._sum(s, project_id=7) == pytest.approx(0.21)  # оценка + поправка = факт


# ---------------------------------------------------------------- A08: переключатели режима убраны

def test_pytest_env_is_not_a_switch(monkeypatch):
    assert "PYTEST_CURRENT_TEST" in os.environ  # мы под pytest
    monkeypatch.setattr(media_spend, "TEST_FORCE", True)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "real-looking-key")
    assert media_spend.disabled_reason("elevenlabs") is None
    assert media_spend._enabled("elevenlabs") is True


@pytest.mark.parametrize("provider", ["elevenlabs"])
async def test_synthetic_key_does_not_disable_accounting_but_tags_the_row(ledger, monkeypatch, provider):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "synthetic-key-1")
    media_spend.record(provider, usd=0.2, note="tts")
    rows = [r for r in await _rows(ledger) if r.source == "media_native"]
    assert len(rows) == 1 and "synthetic_key=true" in rows[0].note and rows[0].kind == "estimated"


def test_explicit_setting_switches_recording_off_visibly(monkeypatch, log_lines):
    monkeypatch.setattr(settings, "media_spend_recording", False)
    assert media_spend.disabled_reason("kie") == "setting_off"
    assert media_spend._enabled("kie") is False
    assert media_spend.skipped_counts() == {"kie:setting_off": 1}
    assert any("NOT recorded" in ln for ln in log_lines)
    assert outsee_guard.browser_enabled() is False


def test_sandbox_ledger_target_is_refused_and_counted(monkeypatch):
    monkeypatch.setattr(media_spend, "TEST_FORCE", False)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "real-looking-key")
    monkeypatch.setattr(settings, "sqlite_path", Path(settings.data_dir) / "other.db")  # движок смотрит в другую БД
    assert media_spend.disabled_reason("elevenlabs") == "ledger_target_mismatch"
    assert media_spend._enabled("elevenlabs") is False
    assert media_spend.skipped_counts()["elevenlabs:ledger_target_mismatch"] == 1


def test_no_key_means_nothing_to_bill(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    assert media_spend.disabled_reason("elevenlabs") == "no_key"


def test_no_pytest_or_synthetic_switch_left_in_recording_modules():
    for rel in ("app/services/media_spend.py", "app/services/outsee_guard.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        code = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(("#", '"""')))
        assert 'in os.environ' not in code, rel
        assert 'startswith("synthetic")' not in code.replace("return _provider_key(provider).startswith", ""), rel


async def test_outsee_guard_intent_write_failure_is_traced(ledger):
    ctx = outsee_guard.Ctx(7, "frame_01", "m", "image", "ph")

    async def boom(session, **kw):
        raise RuntimeError("locked")

    ok = await outsee_guard._append(boom, project_id=7, key="frame_01", event="intent")
    assert ok is False and media_spend.gap_count() == 1
    rows = await _rows(ledger)
    assert any(r.source == "write_failed" and "intent_row" in r.note for r in rows)
    assert ctx.key == "frame_01"


# ---------------------------------------------------------------- A08: трекер

@pytest.fixture
def tracker(tmp_path, monkeypatch):
    db = tmp_path / "trk" / "tracker.db"
    monkeypatch.setattr(hook, "_TRACKER_DB", db)
    monkeypatch.setattr(hook, "_get_identity", lambda: ("alice-os-user", "ALICE-PC"))
    return db


def _tracker_rows(db: Path):
    with sqlite3.connect(str(db)) as c:
        return c.execute("SELECT user_name, device_name, synced, metadata_json, model FROM api_calls "
                         "WHERE call_type != 'system'").fetchall()


def test_tracker_write_failure_is_not_silent_and_row_is_recoverable(tracker, log_lines):
    with patch.object(hook, "_insert_local", side_effect=sqlite3.OperationalError("database is locked")):
        hook.record_api_call(provider="kie", model="gpt-5.6-sol", prompt_tokens=1000, completion_tokens=100)
        assert hook.flush(10)
    assert hook.write_failure_count() >= 1
    spilled = [json.loads(ln) for ln in (tracker.parent / "tracker_spill.jsonl").read_text("utf-8").splitlines()]
    assert spilled[-1]["model"] == "gpt-5.6-sol"
    assert any(r["what"] == "api_calls_row" and r["usd"] > 0 for r in _gap_file_rows())  # пробел в журнале с суммой
    assert any("НЕ записана" in ln for ln in log_lines)
    assert not tracker.exists() or _tracker_rows(tracker) == []
    # база снова доступна: следующая запись возвращает и потерянную строку
    hook.record_api_call(provider="kie", model="other-model")
    assert hook.flush(10)
    models = sorted(r[4] for r in _tracker_rows(tracker))
    assert models == ["gpt-5.6-sol", "other-model"]
    assert not (tracker.parent / "tracker_spill.jsonl").exists()


def test_tracker_queue_survives_shutdown_threads_are_not_daemon(tracker, monkeypatch):
    seen: dict = {}
    real_thread = threading.Thread

    def spy(*a, **kw):
        seen.update(kw)
        return real_thread(*a, **kw)

    monkeypatch.setattr(hook.threading, "Thread", spy)
    hook.record_api_call(provider="kie", model="m")
    assert seen.get("daemon") is False  # интерпретатор дожидается записи при остановке
    assert hook.flush(10) and hook.pending_writes() == 0
    assert len(_tracker_rows(tracker)) == 1


def test_thread_start_failure_spills_instead_of_losing(tracker, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(hook.threading, "Thread", broken)
    hook.record_api_call(provider="kie", model="m-spill")
    assert hook.pending_writes() == 0
    spilled = (tracker.parent / "tracker_spill.jsonl").read_text("utf-8")
    assert "m-spill" in spilled


def test_cost_kind_estimated_vs_actual_in_row_metadata(tracker):
    hook.record_api_call(provider="kie", model="gpt-5.6-sol", prompt_tokens=100, completion_tokens=10)
    hook.record_api_call(provider="kie", model="gpt-5.6-sol", cost_usd=0.0123)
    assert hook.flush(10)
    kinds = sorted(json.loads(r[3])["cost_kind"] for r in _tracker_rows(tracker))
    assert kinds == ["actual", "estimated"]


def test_track_api_cost_failure_leaves_a_gap(monkeypatch):
    monkeypatch.setattr(settings, "gpt_api_key", "synthetic-key")
    with patch("app.services.api_tracker_hook.record_api_call", side_effect=RuntimeError("boom")):
        _track_api_cost(model="m", usage=None, duration_sec=0.1, project_id=3)  # не бросает
    assert media_spend.gap_count() == 1
    assert media_spend.recent_gaps()[0]["what"] == "track_api_cost"


def test_track_api_cost_synthetic_key_is_recorded_and_tagged(monkeypatch):
    monkeypatch.setattr(settings, "gpt_api_key", "synthetic-key")
    with patch("app.services.api_tracker_hook.record_api_call") as rec:
        _track_api_cost(model="m", usage={"input_tokens": 5, "output_tokens": 2}, duration_sec=0.1, project_id=3)
    meta = rec.call_args.kwargs["metadata"]
    assert meta["synthetic_key"] is True and meta["cost_kind"] == "estimated"


def test_track_api_cost_recording_setting_off_skips(monkeypatch):
    monkeypatch.setattr(settings, "gpt_api_key", "real-key")
    monkeypatch.setattr(settings, "media_spend_recording", False)
    with patch("app.services.api_tracker_hook.record_api_call") as rec:
        _track_api_cost(model="m", usage=None, duration_sec=0.1, project_id=3)
    rec.assert_not_called()


# ---------------------------------------------------------------- A17: облако

def test_cloud_defaults_are_off_and_have_no_embedded_credentials(monkeypatch):
    for name in ("API_TRACKER_CLOUD_ENABLED", "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(name, raising=False)
    fresh = Settings(_env_file=None)
    assert fresh.api_tracker_cloud_enabled is False
    assert fresh.api_tracker_supabase_url == "" and fresh.api_tracker_supabase_key == ""
    assert fresh.api_tracker_cloud_send_identity is False
    for rel in ("app/services/api_tracker_hook.py", "api-tracker/app/config.py", "app/settings.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "jubhhajwknvhlntmwpoj" not in text and "sb_publishable_" not in text, rel


def test_settings_read_supabase_key_aliases(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.invalid")
    monkeypatch.delenv("SUPABASE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-k")
    fresh = Settings(_env_file=None)
    assert fresh.api_tracker_supabase_url == "https://example.invalid" and fresh.api_tracker_supabase_key == "anon-k"


def test_no_outgoing_connection_without_the_flag(tracker, monkeypatch):
    """Перехват сокета и httpx: запись, проверка рубильника и его переключение не делают ни одного соединения."""
    monkeypatch.setattr(settings, "api_tracker_cloud_enabled", False)
    monkeypatch.setattr(settings, "api_tracker_supabase_url", "https://example.invalid")
    monkeypatch.setattr(settings, "api_tracker_supabase_key", "k")
    attempts: list = []

    def trap(*a, **kw):
        attempts.append(a)
        raise AssertionError("outgoing connection without API_TRACKER_CLOUD_ENABLED")

    monkeypatch.setattr(socket.socket, "connect", trap)
    monkeypatch.setattr(socket, "create_connection", trap)
    monkeypatch.setattr(socket, "getaddrinfo", trap)
    import httpx
    monkeypatch.setattr(httpx.Client, "send", trap)

    hook.record_api_call(provider="kie", model="m")
    assert hook.flush(10)
    hook.is_killswitch_active(force_refresh=True)
    hook.toggle_killswitch("block", user_name="op", reason="t")
    hook.toggle_killswitch("unblock", user_name="op")
    hook.check_api_allowed("kie", "m")
    assert hook._push_to_supabase({"x": 1}) is False
    assert attempts == []
    assert len(_tracker_rows(tracker)) == 1


class _FakeClient:
    posted: list = []

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, headers=None, json=None):
        _FakeClient.posted.append({"url": url, "headers": headers, "json": json})

        class R:
            status_code = 201
        return R()


def _enable_cloud(monkeypatch, *, identity=False):
    monkeypatch.setattr(settings, "api_tracker_cloud_enabled", True)
    monkeypatch.setattr(settings, "api_tracker_supabase_url", "https://cloud.example.invalid")
    monkeypatch.setattr(settings, "api_tracker_supabase_key", "key-from-settings")
    monkeypatch.setattr(settings, "api_tracker_cloud_send_identity", identity)
    import httpx
    _FakeClient.posted = []
    monkeypatch.setattr(httpx, "Client", _FakeClient)


def test_cloud_row_has_hashed_identity_but_local_row_keeps_names(tracker, monkeypatch):
    _enable_cloud(monkeypatch)
    hook.record_api_call(provider="kie", model="m")
    assert hook.flush(10)
    sent = _FakeClient.posted[0]
    assert sent["url"] == "https://cloud.example.invalid/rest/v1/api_calls"
    assert sent["headers"]["apikey"] == "key-from-settings"
    body = json.dumps(sent["json"], ensure_ascii=False)
    assert "alice-os-user" not in body and "ALICE-PC" not in body
    assert sent["json"]["user_name"].startswith("u-") and sent["json"]["device_name"].startswith("d-")
    assert sent["json"]["user_name"] != sent["json"]["device_name"]
    user, device, synced, *_ = _tracker_rows(tracker)[0]
    assert (user, device, synced) == ("alice-os-user", "ALICE-PC", 1)  # локально имена как раньше


def test_hash_is_stable_per_install_and_salted(tracker, monkeypatch):
    a = hook._cloud_identity("alice-os-user", "ALICE-PC")
    assert a == hook._cloud_identity("alice-os-user", "ALICE-PC")
    salt_file = tracker.parent / ".cloud_identity_salt"
    assert salt_file.is_file()
    salt_file.write_text("another-salt", encoding="utf-8")
    assert hook._cloud_identity("alice-os-user", "ALICE-PC") != a  # без соли хэш не восстановить подбором имени


def test_identity_without_salt_is_empty_not_plain(tracker, monkeypatch):
    monkeypatch.setattr(hook, "_cloud_salt", lambda: "")
    assert hook._cloud_identity("alice-os-user", "ALICE-PC") == ("", "")


def test_explicit_opt_in_sends_identity_as_is(tracker, monkeypatch):
    _enable_cloud(monkeypatch, identity=True)
    hook.record_api_call(provider="kie", model="m")
    assert hook.flush(10)
    assert _FakeClient.posted[0]["json"]["user_name"] == "alice-os-user"


def test_killswitch_cloud_payload_hashes_identity(tracker, monkeypatch):
    _enable_cloud(monkeypatch)
    hook.toggle_killswitch("block", user_name="alice-os-user", reason="t")
    body = json.dumps(_FakeClient.posted[0]["json"], ensure_ascii=False)
    assert "alice-os-user" not in body and "ALICE-PC" not in body


def test_cloud_on_without_credentials_sends_nothing_and_says_so(tracker, monkeypatch, log_lines):
    monkeypatch.setattr(settings, "api_tracker_cloud_enabled", True)
    monkeypatch.setattr(settings, "api_tracker_supabase_url", "")
    monkeypatch.setattr(settings, "api_tracker_supabase_key", "")
    hook._cloud_warned.discard("no_credentials")
    assert hook._push_to_supabase({"x": 1}) is False
    assert any("SUPABASE_URL" in ln for ln in log_lines)


def test_unreadable_cloud_setting_means_off(monkeypatch):
    class Boom:
        @property
        def api_tracker_cloud_enabled(self):
            raise RuntimeError("settings broken")

    with patch("app.settings.settings", Boom()):
        assert hook._cloud_enabled() is False


def _load_tracker_config(monkeypatch, env: dict):
    """api-tracker/app/config.py без чтения .env: вызов загрузчика вырезан из исходника."""
    for name in ("API_TRACKER_CLOUD_ENABLED", "SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY"):
        monkeypatch.delenv(name, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    src = (ROOT / "api-tracker" / "app" / "config.py").read_text(encoding="utf-8")
    assert "\n_load_env_file()\n" in src
    ns: dict = {"__file__": str(ROOT / "api-tracker" / "app" / "config.py"), "__name__": "tracker_config_under_test"}
    exec(compile(src.replace("\n_load_env_file()\n", "\n"), "config.py", "exec"), ns)
    return ns


def test_standalone_tracker_config_is_off_without_the_flag(monkeypatch):
    ns = _load_tracker_config(monkeypatch, {"SUPABASE_URL": "https://x.invalid", "SUPABASE_KEY": "k"})
    assert ns["cloud_enabled"]() is False
    assert ns["get_supabase_url"]() == "" and ns["get_supabase_key"]() == ""  # cloud_sync.py тогда не соединяется


def test_standalone_tracker_config_with_flag_and_owner_credentials(monkeypatch):
    ns = _load_tracker_config(monkeypatch, {"API_TRACKER_CLOUD_ENABLED": "true", "SUPABASE_URL": "https://x.invalid",
                                            "SUPABASE_ANON_KEY": "anon"})
    assert ns["get_supabase_url"]() == "https://x.invalid" and ns["get_supabase_key"]() == "anon"
    assert ns["DEFAULT_SUPABASE_URL"] == "" and ns["DEFAULT_SUPABASE_KEY"] == ""
