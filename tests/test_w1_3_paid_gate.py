from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
from app.services import kill_switch, media_spend, spend_guard
from app.settings import settings

REPO = Path(__file__).resolve().parent.parent




@pytest.fixture
async def db(tmp_path, monkeypatch):
    from app.services import actions_v1

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(spend_guard, "_sessions", lambda: sessions)
    import app.db
    monkeypatch.setattr(app.db, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 0.0)
    monkeypatch.setattr(settings, "spend_project_cap_usd", 0.0)
    monkeypatch.setattr(settings, "spend_run_cap_usd", 0.0)
    from app.services import disk_guard
    monkeypatch.setattr(disk_guard, "check", lambda *a, **k: disk_guard.DiskCheck(True, "ok", "ok"))
    spend_guard.reset_inflight()
    kill_switch.reset_cache()
    yield sessions
    spend_guard.reset_inflight()
    kill_switch.reset_cache()
    await engine.dispose()


async def _kill(sessions, on=True):
    await kill_switch.set_state(sessions, active=on, actor="test-operator", rationale="w1-3 test stop")


async def _ledger(sessions) -> list[dict]:
    async with sessions() as s:
        rows = (await s.execute(text(
            "SELECT kind, provider, source, usd, project_id, note FROM spend_ledger ORDER BY id"))).all()
    return [dict(kind=r[0], provider=r[1], source=r[2], usd=r[3], project_id=r[4], note=r[5]) for r in rows]


async def _add_ledger(sessions, *, kind="actual", usd=0.0, project_id=None, run_id="", note=""):
    async with sessions() as s:
        await spend_guard.ensure_schema(s)
        s.add(spend_guard.SpendLedger(day=spend_guard._today(), kind=kind, provider="kie", project_id=project_id,
                                      run_id=run_id, source="media_native", usd=usd, note=note))
        await s.commit()


@pytest.fixture
def no_recording(monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(media_spend, "_enabled", lambda provider: False)
    monkeypatch.setattr(media_spend, "record", lambda provider, **kw: seen.append({"provider": provider, **kw}))
    return seen


@pytest.fixture
def real_recording(monkeypatch):
    monkeypatch.setattr(media_spend, "TEST_FORCE", True)
    monkeypatch.setattr(media_spend, "_enabled", lambda provider: True)
    if hasattr(media_spend, "disabled_reason"):
        monkeypatch.setattr(media_spend, "disabled_reason", lambda provider: None)


class _Resp:
    def __init__(self, status=200, payload=None, content=b"x" * 64):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = content
        self.headers = {}
        self.text = str(self._payload)

    def json(self):
        return self._payload


def _fake_client(responder, calls):
    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, **kw):
            calls.append(("POST", str(url)))
            return responder(url)

        async def get(self, url, **kw):
            calls.append(("GET", str(url)))
            return responder(url)

    return FakeClient




async def test_gate_allows_and_tracks_inflight(db):
    ticket = await spend_guard.paid_gate(provider="kie", what="t", estimate_usd=0.2)
    assert ticket.estimate_usd == pytest.approx(0.2)
    assert spend_guard.inflight_usd()["count"] == 1
    ticket.release()
    assert spend_guard.inflight_usd()["count"] == 0


async def test_kill_switch_blocks_gate_and_leaves_audit_row(db):
    await _kill(db)
    with pytest.raises(spend_guard.PaidCallBlocked) as e:
        await spend_guard.paid_gate(provider="gpt", what="llm request")
    assert e.value.code == spend_guard.BLOCK_KILL_SWITCH
    assert "рубильник" in e.value.message_ru and "не отправлялся" in e.value.message_ru
    assert e.value.context["no_resubmit"] is True and e.value.context["request_sent"] is False
    rows = await _ledger(db)
    assert [r["source"] for r in rows] == ["paid_gate_block"] and rows[0]["usd"] == 0
    with pytest.raises(spend_guard.PaidCallBlocked):
        await spend_guard.paid_gate(provider="gpt", what="llm request")
    assert len(await _ledger(db)) == 1
    await _kill(db, on=False)
    (await spend_guard.paid_gate(provider="gpt", what="llm request")).release()


async def test_unreadable_kill_switch_state_blocks_fail_closed(db, monkeypatch):
    class Broken:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise OSError("disk I/O error")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(spend_guard, "_sessions", lambda: Broken())
    with pytest.raises(spend_guard.PaidCallBlocked) as e:
        await spend_guard.paid_gate(provider="kie")
    assert e.value.code == spend_guard.BLOCK_CHECK_FAILED
    assert spend_guard.inflight_usd()["count"] == 0


async def test_locked_database_is_not_read_as_switch_off(db):
    class LockedSession:
        async def scalar(self, *a, **k):
            raise OperationalError("SELECT", {}, Exception("database is locked"))

        async def rollback(self):
            pass

    with pytest.raises(OperationalError):
        await kill_switch.is_active(LockedSession())

    class MissingSession(LockedSession):
        async def scalar(self, *a, **k):
            raise OperationalError("SELECT", {}, Exception("no such table: supervisor_kill_switch"))

    assert await kill_switch.is_active(MissingSession()) is False


async def test_missing_tables_read_as_nothing_spent_and_switch_off(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}", poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(spend_guard, "_sessions", lambda: sessions)
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 5.0)
    monkeypatch.setattr(settings, "spend_project_cap_usd", 0.0)
    monkeypatch.setattr(settings, "spend_run_cap_usd", 0.0)
    from app.services import disk_guard
    monkeypatch.setattr(disk_guard, "check", lambda *a, **k: disk_guard.DiskCheck(True, "ok", "ok"))
    spend_guard.reset_inflight()
    try:
        (await spend_guard.paid_gate(provider="kie", estimate_usd=0.1)).release()
    finally:
        await engine.dispose()


async def test_daily_cap_counts_facts_open_reserves_and_unknown_rows(db, monkeypatch):
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 1.0)
    await _add_ledger(db, kind="actual", usd=0.40)
    await _add_ledger(db, kind="reserve", usd=0.30)
    (await spend_guard.paid_gate(provider="kie", estimate_usd=0.2, register=False)).release()
    with pytest.raises(spend_guard.PaidCallBlocked) as e:
        await spend_guard.paid_gate(provider="kie", estimate_usd=0.4)
    assert e.value.code == "spend_cap_daily"
    assert e.value.detail["spent"] == pytest.approx(0.70) and e.value.detail["cap"] == 1.0
    await _add_ledger(db, kind="estimated", usd=0.0, note="video m ok cost_unknown=true")
    with pytest.raises(spend_guard.PaidCallBlocked):
        await spend_guard.paid_gate(provider="kie", estimate_usd=0.1)
    monkeypatch.setenv("PAID_GATE_UNKNOWN_ROW_USD", "0.05")
    (await spend_guard.paid_gate(provider="kie", estimate_usd=0.1)).release()


async def test_settle_lowers_the_counted_total(db, monkeypatch):
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 1.0)
    await _add_ledger(db, kind="reserve", usd=0.9)
    with pytest.raises(spend_guard.PaidCallBlocked):
        await spend_guard.paid_gate(provider="kie", estimate_usd=0.3)
    await _add_ledger(db, kind="settle", usd=-0.7)
    (await spend_guard.paid_gate(provider="kie", estimate_usd=0.3)).release()


async def test_project_and_run_caps(db, monkeypatch):
    monkeypatch.setattr(settings, "spend_project_cap_usd", 1.0)
    monkeypatch.setattr(settings, "spend_run_cap_usd", 0.5)
    await _add_ledger(db, kind="actual", usd=0.9, project_id=7)
    with pytest.raises(spend_guard.PaidCallBlocked) as e:
        await spend_guard.paid_gate(provider="kie", estimate_usd=0.3, project_id=7)
    assert e.value.code == "spend_cap_project"
    (await spend_guard.paid_gate(provider="kie", estimate_usd=0.3, project_id=8)).release()
    await _add_ledger(db, kind="actual", usd=0.4, project_id=9, run_id="run-1")
    with pytest.raises(spend_guard.PaidCallBlocked) as e:
        await spend_guard.paid_gate(provider="kie", estimate_usd=0.3, project_id=9, run_id="run-1")
    assert e.value.code == "spend_cap_run"
    (await spend_guard.paid_gate(provider="kie", estimate_usd=0.3, project_id=9, run_id="run-2")).release()


async def test_inflight_estimates_count_until_released(db, monkeypatch):
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 1.0)
    first = await spend_guard.paid_gate(provider="kie", estimate_usd=0.6)
    with pytest.raises(spend_guard.PaidCallBlocked) as e:
        await spend_guard.paid_gate(provider="outsee", estimate_usd=0.6)
    assert e.value.code == "spend_cap_daily"
    first.release()
    (await spend_guard.paid_gate(provider="outsee", estimate_usd=0.6)).release()


async def test_nested_gate_of_same_provider_is_not_counted_twice(db, monkeypatch):
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 1.0)
    async with spend_guard.paid_call("outsee", "outer", estimate_usd=0.6):
        (await spend_guard.paid_gate(provider="outsee", what="inner", estimate_usd=0.6)).release()
        with pytest.raises(spend_guard.PaidCallBlocked):
            await spend_guard.paid_gate(provider="kie", estimate_usd=0.6)
        await _kill(db)
        with pytest.raises(spend_guard.PaidCallBlocked):
            await spend_guard.paid_gate(provider="outsee", what="inner")
    assert spend_guard.inflight_usd()["count"] == 0


async def test_paid_call_releases_inflight_on_error(db):
    with pytest.raises(ValueError):
        async with spend_guard.paid_call("kie", "x", estimate_usd=0.2):
            assert spend_guard.inflight_usd()["count"] == 1
            raise ValueError("boom")
    assert spend_guard.inflight_usd()["count"] == 0


async def test_disk_hook_blocks_and_missing_module_allows(db, monkeypatch):
    from app.services import disk_guard
    monkeypatch.setattr(disk_guard, "check", lambda *a, **k: disk_guard.DiskCheck(
        False, "low", "Мало места на диске: свободно 4,0 ГБ."))
    with pytest.raises(spend_guard.PaidCallBlocked) as e:
        await spend_guard.paid_gate(provider="kie")
    assert e.value.code == spend_guard.BLOCK_DISK and "Мало места" in e.value.message_ru

    def boom(*a, **k):
        raise RuntimeError("check bug")

    monkeypatch.setattr(disk_guard, "check", boom)
    with pytest.raises(spend_guard.PaidCallBlocked) as e:
        await spend_guard.paid_gate(provider="kie")
    assert e.value.code == spend_guard.BLOCK_CHECK_FAILED

    import app.services as services_pkg
    monkeypatch.delattr(services_pkg, "disk_guard")
    monkeypatch.setitem(sys.modules, "app.services.disk_guard", None)
    (await spend_guard.paid_gate(provider="kie")).release()


async def test_precheck_returns_dict_and_keeps_no_inflight(db):
    assert (await spend_guard.paid_precheck("outsee", "create video"))["allowed"] is True
    assert spend_guard.inflight_usd()["count"] == 0
    await _kill(db)
    verdict = await spend_guard.paid_precheck("outsee", "create video")
    assert verdict["allowed"] is False and verdict["code"] == spend_guard.BLOCK_KILL_SWITCH


async def test_cap_check_database_failure_blocks(db, monkeypatch):
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 5.0)

    async def broken(*a, **k):
        raise OperationalError("SELECT", {}, Exception("database is locked"))

    monkeypatch.setattr(spend_guard, "conservative_sums", broken)
    with pytest.raises(spend_guard.PaidCallBlocked) as e:
        await spend_guard.paid_gate(provider="kie")
    assert e.value.code == spend_guard.BLOCK_CHECK_FAILED




async def test_seam_kie_create_task(db, monkeypatch, no_recording):
    from app.bots import kie_http

    calls: list = []

    async def fake_post(url, body, timeout=120.0):
        calls.append(url)
        return _Resp(200), {"code": 200, "data": {"taskId": "t-1"}}, "test"

    monkeypatch.setattr(kie_http, "kie_post_json", fake_post)
    monkeypatch.setattr(kie_http, "kie_api_base_url", lambda: "https://kie.invalid")
    assert await kie_http.create_task("jobs", None, {"model": "m", "input": {}}) == "t-1"
    assert len(calls) == 1 and spend_guard.inflight_usd()["count"] == 0
    await _kill(db)
    with pytest.raises(kie_http.KieHttpError) as e:
        await kie_http.create_task("jobs", None, {"model": "m", "input": {}})
    assert len(calls) == 1
    assert e.value.context["no_resubmit"] is True and e.value.context["gate_code"] == spend_guard.BLOCK_KILL_SWITCH
    assert no_recording == [] or all("rejected" not in str(x) for x in no_recording)


async def test_seam_kie_kling_create_task_is_gated(db, monkeypatch, no_recording):
    from app.bots import kie_kling

    calls: list = []

    async def fake_post(url, body, timeout=120.0):
        calls.append(url)
        return _Resp(200), {"code": 200, "data": {"taskId": "k-1"}}, "test"

    monkeypatch.setattr(kie_kling, "kie_post_json", fake_post)
    monkeypatch.setattr(kie_kling, "kie_api_base_url", lambda: "https://kie.invalid")
    body = {"model": "kling-2.6/text-to-video", "input": {"prompt": "p", "duration": 5}}
    assert await kie_kling._create_task(body) == "k-1"
    assert len(calls) == 1 and spend_guard.inflight_usd()["count"] == 0
    await _kill(db)
    with pytest.raises(kie_kling.KieKlingError) as e:
        await kie_kling._create_task(body)
    assert len(calls) == 1
    assert e.value.context["no_resubmit"] is True and e.value.context["gate_code"] == spend_guard.BLOCK_KILL_SWITCH
    await _kill(db, on=False)
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 0.01)
    with pytest.raises(kie_kling.KieKlingError) as e2:
        await kie_kling._create_task(body)
    assert len(calls) == 1 and e2.value.context["gate_code"] == "spend_cap_daily"


async def test_seam_without_run_id_still_counts_against_the_run_cap(db, monkeypatch, no_recording):
    from app.bots import kie_http
    from app.services import request_context

    calls: list = []

    async def fake_post(url, body, timeout=120.0):
        calls.append(url)
        return _Resp(200), {"code": 200, "data": {"taskId": "t-run"}}, "test"

    monkeypatch.setattr(kie_http, "kie_post_json", fake_post)
    monkeypatch.setattr(kie_http, "kie_api_base_url", lambda: "https://kie.invalid")
    monkeypatch.setattr(settings, "spend_run_cap_usd", 1.0)
    await _add_ledger(db, kind="actual", usd=0.9, run_id="run-c8")
    request_context.bind_run("run-c8")
    try:
        with pytest.raises(kie_http.KieHttpError) as e:
            await kie_http.create_task("jobs", None, {"model": "m", "input": {}})
        assert calls == [] and e.value.context["gate_code"] == "spend_cap_run"
    finally:
        request_context.unbind_run("run-c8")
    assert await kie_http.create_task("jobs", None, {"model": "m", "input": {}}) == "t-run"
    assert len(calls) == 1


async def test_seam_outsee_post_generate(db, monkeypatch):
    from app.bots import outsee_http as oh

    calls: list = []
    monkeypatch.setattr(oh.httpx, "AsyncClient", _fake_client(lambda url: _Resp(200, {"id": 5, "status": "queued"}), calls))
    monkeypatch.setattr(oh, "_headers", lambda: {"Authorization": "Bearer test"})
    monkeypatch.setattr(oh, "_base_url", lambda: "https://outsee.invalid")
    assert (await oh._post_generate("/api/v1/videos/generate", {"prompt": "p"}))["id"] == 5
    assert len(calls) == 1 and spend_guard.inflight_usd()["count"] == 0
    await _kill(db)
    with pytest.raises(spend_guard.PaidCallBlocked) as e:
        await oh._post_generate("/api/v1/images/generate", {"prompt": "p"})
    assert len(calls) == 1
    assert not isinstance(e.value, oh.OutseeHttpError)


async def test_seam_outsee_cap_uses_video_estimate(db, monkeypatch):
    from app.bots import outsee_http as oh

    calls: list = []
    monkeypatch.setattr(oh.httpx, "AsyncClient", _fake_client(lambda url: _Resp(200, {"id": 1}), calls))
    monkeypatch.setattr(oh, "_headers", lambda: {"Authorization": "Bearer test"})
    monkeypatch.setattr(oh, "_base_url", lambda: "https://outsee.invalid")
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 0.5)
    with pytest.raises(spend_guard.PaidCallBlocked) as e:
        await oh._post_generate("/api/v1/videos/generate", {})
    assert e.value.code == "spend_cap_daily" and calls == []
    await oh._post_generate("/api/v1/images/generate", {})
    assert len(calls) == 1


async def test_seam_gpt_requests(db, monkeypatch):
    from app.services import gpt_api

    calls: list = []
    monkeypatch.setattr(gpt_api, "_async_client", lambda **kw: _fake_client(lambda url: _Resp(200, {}), calls)())
    await _kill(db)
    for fn in (gpt_api._chat_completions_nostream, gpt_api._chat_completions_stream, gpt_api._chat_responses_stream):
        with pytest.raises(gpt_api.GptApiError) as e:
            await fn(url="https://gpt.invalid/v1", headers={}, body={}, timeout=5.0, use_model="m")
        assert e.value.retryable is False and e.value.context["paid_gate_blocked"] is True
        assert e.value.context["no_resubmit"] is True
    assert calls == []


async def test_seam_gpt_chat_inline_post_and_no_retry_loop(db, monkeypatch):
    from app.services import gpt_api

    calls: list = []
    monkeypatch.setattr(gpt_api, "_async_client", lambda **kw: _fake_client(lambda url: _Resp(200, {}), calls)())

    async def no_resolve(url):
        return None

    monkeypatch.setattr(gpt_api, "_ensure_url_host_resolves", no_resolve)
    monkeypatch.setattr(gpt_api, "is_responses_mode", lambda: False)
    monkeypatch.setattr(gpt_api, "_headers", lambda *_a, **_k: {"Authorization": "Bearer test"})
    monkeypatch.setattr(gpt_api, "_chat_url", lambda model: "https://gpt.invalid/v1/chat/completions")
    await _kill(db)
    with pytest.raises(gpt_api.GptApiError) as e:
        await gpt_api.chat(prompt="hi", auto_pack=False, max_retries=3)
    assert e.value.context["paid_gate_blocked"] is True
    assert calls == []


async def test_seam_elevenlabs_tts(db, monkeypatch, tmp_path, no_recording):
    from app.services import elevenlabs_api as el

    calls: list = []
    monkeypatch.setattr(el.httpx, "AsyncClient", _fake_client(lambda url: _Resp(200, content=b"ID3" * 40), calls))
    monkeypatch.setattr(el, "_resolve_api_key", lambda: "k-test")
    monkeypatch.setattr(el, "_resolve_proxy_url", lambda: None)
    await _kill(db)
    out = tmp_path / "a.mp3"
    with pytest.raises(el.ElevenLabsApiError) as e:
        await el.synthesize_speech("Привет, это проверка озвучки.", out)
    assert calls == [] and not out.exists()
    assert e.value.context["no_resubmit"] is True
    assert not isinstance(e.value, el.ElevenLabsUncertainError)
    assert not list(tmp_path.glob(".tts_uncertain_*"))


async def test_seam_sfx_post(db, monkeypatch, tmp_path, no_recording):
    from app.services import sfx_gen

    calls: list = []
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(lambda url: _Resp(200, content=b"RIFF" * 20), calls))
    monkeypatch.setattr(settings, "elevenlabs_api_key", "k-test", raising=False)
    out = tmp_path / "s.mp3"
    assert await sfx_gen._elevenlabs_sfx("whoosh", 2.0, out) == out
    assert len(calls) == 1
    await _kill(db)
    out2 = tmp_path / "s2.mp3"
    with pytest.raises(RuntimeError) as e:
        await sfx_gen._elevenlabs_sfx("whoosh", 2.0, out2)
    assert isinstance(e.value, spend_guard.PaidCallBlocked)
    assert len(calls) == 1 and not out2.exists()


async def test_broken_recording_does_not_affect_a_gated_call(db, monkeypatch, real_recording):
    from app.bots import kie_http

    async def fake_post(url, body, timeout=120.0):
        return _Resp(200), {"code": 200, "data": {"taskId": "t-9"}}, "test"

    def broken_record(*a, **k):
        raise RuntimeError("ledger write failed")

    monkeypatch.setattr(kie_http, "kie_post_json", fake_post)
    monkeypatch.setattr(kie_http, "kie_api_base_url", lambda: "https://kie.invalid")
    monkeypatch.setattr(spend_guard, "record_actual", broken_record)
    assert await kie_http.create_task("jobs", None, {"model": "m", "input": {}}) == "t-9"
    await media_spend.drain()


def test_credit_error_auto_kill_is_on_by_default():
    assert settings.model_fields["spend_auto_kill_on_402"].default is True




async def test_enhance_prompt_blocked_by_kill_switch_makes_no_request(db, monkeypatch):
    from app.web.routers import outsee_create as oc

    calls: list = []
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(lambda url: _Resp(200), calls))
    await _kill(db)
    out = await oc.enhance_prompt_endpoint(oc.EnhancePromptRequest(prompt="кот на крыше"))
    assert calls == []
    assert out["provider"] == "synthesizer" and out["paid_blocked"]["code"] == spend_guard.BLOCK_KILL_SWITCH
    assert "рубильник" in out["paid_blocked"]["message_ru"]


async def test_enhance_prompt_kie_chat_writes_ledger_row(db, monkeypatch, real_recording):
    from app.bots import kie_http, kie_kling
    from app.services import gpt_client
    from app.web.routers import outsee_create as oc

    calls: list = []
    payload = {"choices": [{"message": {"content": "A highly detailed cinematic photo of a cat on a roof at golden hour"}}]}
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(lambda url: _Resp(200, payload), calls))
    monkeypatch.setattr(gpt_client, "gpt_text_via_api", lambda: False)
    monkeypatch.setattr(kie_http, "kie_configured", lambda: True)
    monkeypatch.setattr(kie_kling, "kie_api_key", lambda: "k-test")
    monkeypatch.setattr(kie_kling, "kie_api_base_url", lambda: "https://kie.invalid")
    out = await oc.enhance_prompt_endpoint(oc.EnhancePromptRequest(prompt="кот на крыше"))
    assert out["provider"] == "kie-llm" and len(calls) == 1
    await media_spend.drain()
    rows = [r for r in await _ledger(db) if r["kind"] in ("actual", "estimated")]
    assert len(rows) == 1 and rows[0]["provider"] == "kie" and rows[0]["usd"] > 0
    assert "enhance-prompt" in rows[0]["note"]
    assert spend_guard.inflight_usd()["count"] == 0


async def test_enhance_prompt_kie_402_engages_kill_switch(db, monkeypatch, real_recording):
    from app.bots import kie_http, kie_kling
    from app.services import gpt_client
    from app.web.routers import outsee_create as oc

    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(lambda url: _Resp(402, {"msg": "no credits"}), []))
    monkeypatch.setattr(gpt_client, "gpt_text_via_api", lambda: False)
    monkeypatch.setattr(kie_http, "kie_configured", lambda: True)
    monkeypatch.setattr(kie_kling, "kie_api_key", lambda: "k-test")
    monkeypatch.setattr(kie_kling, "kie_api_base_url", lambda: "https://kie.invalid")
    monkeypatch.setattr(settings, "spend_auto_kill_on_402", True)
    out = await oc.enhance_prompt_endpoint(oc.EnhancePromptRequest(prompt="кот"))
    assert out["provider"] == "synthesizer"
    await media_spend.drain()
    async with db() as s:
        assert await kill_switch.is_active(s) is True
    rows = await _ledger(db)
    assert any(r["usd"] == 0 and "not billed" in r["note"] for r in rows)


async def test_create_generate_route_refuses_before_queueing(db, monkeypatch):
    from fastapi import HTTPException
    from app.web.routers import outsee_http as route

    queued: list = []

    async def fake_enqueue(**kw):
        queued.append(kw)
        return types.SimpleNamespace(to_dict=lambda: {"id": "j1"})

    monkeypatch.setattr(route, "enqueue_generation", fake_enqueue)
    monkeypatch.setattr(route.oh, "outsee_api_configured", lambda: True)
    monkeypatch.setattr(route.oh, "studio_id_to_outsee_video_slug", lambda m: "veo-3-1-lite")
    body = route.OutseeGenerateBody(prompt="p", media="video")
    await _kill(db)
    with pytest.raises(HTTPException) as e:
        await route.outsee_generate(body)
    assert e.value.status_code == 423 and "рубильник" in str(e.value.detail)
    assert queued == []


async def test_create_generate_video_writes_ledger_row(db, monkeypatch, tmp_path, real_recording):
    from app.web.routers import outsee_http as route

    queued: list = []

    async def fake_enqueue(**kw):
        queued.append(kw)
        return types.SimpleNamespace(to_dict=lambda: {"id": "j1"})

    async def fake_generate_video(prompt, out_path, **kw):
        return types.SimpleNamespace(file_path=out_path)

    monkeypatch.setattr(route, "enqueue_generation", fake_enqueue)
    monkeypatch.setattr(route.oh, "outsee_api_configured", lambda: True)
    monkeypatch.setattr(route.oh, "studio_id_to_outsee_video_slug", lambda m: "veo-3-1-lite")
    monkeypatch.setattr(route.oh, "generate_video", fake_generate_video)
    await route.outsee_generate(route.OutseeGenerateBody(prompt="p", media="video"))
    assert len(queued) == 1
    await queued[0]["run"](tmp_path / "v.mp4")
    await media_spend.drain()
    rows = [r for r in await _ledger(db) if r["kind"] in ("actual", "estimated")]
    assert len(rows) == 1 and rows[0]["provider"] == "outsee" and "video" in rows[0]["note"]
    assert "cost_unknown=true" in rows[0]["note"]


async def test_bot_video_seam_writes_ledger_row_and_browser_gate(db, monkeypatch, tmp_path, real_recording):
    from app.bots import outsee as bot
    from app.bots import outsee_http as oh

    async def fake_generate_video(**kw):
        return types.SimpleNamespace(file_path=kw["out_path"])

    monkeypatch.setattr(oh, "outsee_http_enabled", lambda: True)
    monkeypatch.setattr(oh, "generate_video", fake_generate_video)
    res = await bot.OutseeBot._try_http_video(object(), prompt="p", out_path=tmp_path / "v.mp4", project_id=None)
    assert res is not None
    await media_spend.drain()
    rows = [r for r in await _ledger(db) if r["kind"] in ("actual", "estimated")]
    assert len(rows) == 1 and rows[0]["provider"] == "outsee" and "video" in rows[0]["note"]

    ran: list = []

    @bot._browser_guarded("video")
    async def paid_method(self, prompt, out_path, **kw):
        ran.append(1)
        return "clicked"

    await _kill(db)
    with pytest.raises(spend_guard.PaidCallBlocked):
        await paid_method(object(), "p", tmp_path / "x.mp4", project_id=None)
    assert ran == []



_SEND = re.compile(
    r"^(?!\s*@).*(\.post\(|\.stream\(|client\.request\(|urlopen\(|aiohttp\.ClientSession|"
    r"httpx\.(post|put|get|stream|request)\()", re.M)

_GATE_MARK = re.compile(r"spend_guard\.paid_(seam|call|gate)|_gpt_seam")

EXCEPTIONS = {
    "app/bots/morelogin.py": "локальный API антидетект-браузера на этой же машине, оплаты нет",
    "app/bots/chrome_cdp.py": "локальный отладочный порт Chrome, оплаты нет",
    "app/fleet/client.py": "управляющий канал к собственным узлам флота, провайдерской оплаты нет",
    "app/web/routers/fleet.py": "управляющий канал к собственным узлам флота, провайдерской оплаты нет",
    "app/telegram/bot.py": "сессия aiogram для Telegram: уведомления, оплаты нет",
    "app/services/agent_harness.py": "GET по локальному адресу самопроверки",
    "app/services/action_layer/context.py": "внутренний ASGI-транспорт к собственному API слоя действий",
    "app/services/nvidia_asr.py": "GET загрузка весов локальной модели, оплаты нет",
    "app/services/api_tracker_hook.py": "телеметрия трекера; платным вызовом не является (облако выключается в A17)",
    "app/services/decision_v1.py": "надзорный слой: платный запрос проходит charge_request, где рубильник и потолок",
    "app/services/media_transport.py": "транспорт Kie для управляемых задач: submit идёт только из kie_http.create_task "
                                       "(шлюз там), остальное статус и скачивание без оплаты",
}

KNOWN_GAPS: dict[str, str] = {}


def test_every_http_sending_module_is_gated_or_listed():
    flagged: dict[str, str] = {}
    for path in sorted((REPO / "app").rglob("*.py")):
        text_ = path.read_text(encoding="utf-8", errors="replace")
        if _SEND.search(text_):
            flagged[path.relative_to(REPO).as_posix()] = text_
    gated = {k for k, v in flagged.items() if _GATE_MARK.search(v)}
    for must in ("app/bots/kie_http.py", "app/bots/outsee_http.py", "app/services/gpt_api.py",
                 "app/services/elevenlabs_api.py", "app/services/sfx_gen.py", "app/web/routers/outsee_create.py"):
        assert must in gated, f"{must}: платный шов без шлюза"
    unlisted = sorted(set(flagged) - gated - set(EXCEPTIONS) - set(KNOWN_GAPS))
    assert unlisted == [], f"модули с исходящими запросами без шлюза и без записи в списке исключений: {unlisted}"
    stale = sorted((set(EXCEPTIONS) | set(KNOWN_GAPS)) & gated)
    assert stale == [], f"в списках исключений числятся модули, которые уже закрыты шлюзом: {stale}"
    assert all(reason.strip() for reason in {**EXCEPTIONS, **KNOWN_GAPS}.values())


def _enclosing_functions(tree):
    import ast
    out = []

    def walk(node, fn):
        for child in ast.iter_child_nodes(node):
            nxt = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn
            if isinstance(child, ast.Call):
                out.append((child, fn))
            walk(child, nxt)
    walk(tree, None)
    return out


def _is_paid_seam_decorated(fn) -> bool:
    import ast
    for dec in getattr(fn, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Attribute) and target.attr == "paid_seam":
            return True
        if isinstance(target, ast.Name) and target.id == "paid_seam":
            return True
    return False


def test_every_paid_post_sender_is_a_decorated_function_not_just_a_file_with_a_mark():
    import ast
    sender = "kie_post_json"
    bad = []
    seen = 0
    for path in sorted((REPO / "app").rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        text_ = path.read_text(encoding="utf-8", errors="replace")
        if sender not in text_:
            continue
        for call, fn in _enclosing_functions(ast.parse(text_)):
            f = call.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            if name != sender:
                continue
            seen += 1
            if fn is None or not _is_paid_seam_decorated(fn):
                bad.append(f"{rel}:{call.lineno} {getattr(fn, 'name', '<module>')}")
    assert seen >= 2, "the scan found no callers of kie_post_json: the check would be vacuous"
    assert bad == [], f"функции, шлющие платный createTask без @paid_seam: {bad}"


def test_other_gated_entry_points_reference_the_gate():
    assert "paid_precheck" in (REPO / "app/web/routers/outsee_http.py").read_text(encoding="utf-8")
    assert "outsee_tracked" in (REPO / "app/web/routers/outsee_http.py").read_text(encoding="utf-8")
    bot_src = (REPO / "app/bots/outsee.py").read_text(encoding="utf-8")
    assert "spend_guard.paid_gate" in bot_src and "outsee_tracked(oh.generate_video" in bot_src




async def _add_attempt(sessions, *, attempt_id, event, project_id=7):
    from app.services.outsee_idempotency import OutseeIdempotency
    async with sessions() as s:
        s.add(OutseeIdempotency(project_id=project_id, key="frame_1", request_sha="x" * 8, attempt_id=attempt_id,
                                event=event, model="gpt-image-2", media="image"))
        await s.commit()


async def test_open_outsee_attempt_counts_in_cap_sums_until_resolved(db, monkeypatch):
    unit = spend_guard.unknown_row_estimate_usd()
    async with db() as s:
        base = await spend_guard.conservative_sums(s, project_id=7, run_id="")
    await _add_attempt(db, attempt_id="a1", event="intent")
    async with db() as s:
        after_intent = await spend_guard.conservative_sums(s, project_id=7, run_id="")
        other_project = await spend_guard.conservative_sums(s, project_id=8, run_id="")
    assert after_intent["project"] == pytest.approx(base["project"] + unit)
    assert after_intent["daily"] == pytest.approx(base["daily"] + unit)
    assert other_project["project"] == 0.0 and other_project["daily"] == pytest.approx(base["daily"] + unit)
    await _add_attempt(db, attempt_id="a1", event="result")
    async with db() as s:
        closed = await spend_guard.conservative_sums(s, project_id=7, run_id="")
    assert closed["project"] == pytest.approx(base["project"])
    await _add_attempt(db, attempt_id="a2", event="uncertain")
    async with db() as s:
        assert (await spend_guard.conservative_sums(s, project_id=7, run_id=""))["project"] == pytest.approx(
            base["project"] + unit)
    await _add_attempt(db, attempt_id="a2", event="released")
    async with db() as s:
        assert (await spend_guard.conservative_sums(s, project_id=7, run_id=""))["project"] == pytest.approx(
            base["project"])


async def test_open_outsee_attempt_can_block_next_paid_call_by_cap(db, monkeypatch):
    unit = spend_guard.unknown_row_estimate_usd()
    monkeypatch.setattr(settings, "spend_project_cap_usd", unit * 1.5)
    await _add_attempt(db, attempt_id="a9", event="intent", project_id=11)
    with pytest.raises(spend_guard.PaidCallBlocked) as e:
        await spend_guard.paid_gate(provider="outsee", estimate_usd=unit, project_id=11)
    assert e.value.code == "spend_cap_project"


async def test_attempt_running_in_this_process_is_not_double_counted(db):
    unit = spend_guard.unknown_row_estimate_usd()
    async with db() as s:
        base = await spend_guard.conservative_sums(s, project_id=7, run_id="")
    await _add_attempt(db, attempt_id="live1", event="intent")
    spend_guard.mark_live_attempt("live1")
    try:
        async with db() as s:
            assert (await spend_guard.conservative_sums(s, project_id=7, run_id=""))["project"] == pytest.approx(
                base["project"])
    finally:
        spend_guard.drop_live_attempt("live1")
    async with db() as s:
        assert (await spend_guard.conservative_sums(s, project_id=7, run_id=""))["project"] == pytest.approx(
            base["project"] + unit)
