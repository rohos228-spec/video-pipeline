"""W3-2 / B05: лента проекта. Сети нет: временная SQLite с настоящими моделями, ASGI-клиент без токена."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services import timeline as tl

pytestmark = pytest.mark.raw_client
P = 7
T0 = datetime(2026, 5, 1, 12, 0, 0)


def _t(minutes: int) -> str:
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S.%f")


async def _make_db(tmp_path, *, drop=(), with_notifications=True):
    import app.models  # noqa: F401
    import app.services.action_layer.store  # noqa: F401
    import app.services.actions_v1  # noqa: F401
    import app.services.decision_state  # noqa: F401
    import app.services.diagnosis  # noqa: F401
    import app.services.observation  # noqa: F401
    import app.services.operator_audit  # noqa: F401
    import app.services.spend_guard  # noqa: F401
    from app.models import Base
    from app.services import publications_store

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    async with sm() as s:
        await publications_store.ensure_schema(s)
    if with_notifications:  # как после миграции 0013_notification_log
        from app.services import notification_log, schema_migrations
        async with eng.begin() as c:
            for ddl in schema_migrations._ddl_statements(notification_log.NOTIFICATION_LOG_DDL):
                await c.exec_driver_sql(ddl)
    return eng, sm


async def _seed(sm):
    async with sm() as s:
        async def run(sql, **kw):
            await s.execute(text(sql), kw)

        # node_runs: нужен workflow_runs(project_id); внешние ключи SQLite не проверяет без PRAGMA
        await run("INSERT INTO workflow_runs(id,workflow_id,project_id,status,nodes_snapshot,edges_snapshot,meta,"
                  "created_at,updated_at) VALUES (1,1,:p,'running','[]','[]','{}',:t,:t)", p=P, t=_t(0))
        await run("INSERT INTO workflow_runs(id,workflow_id,project_id,status,nodes_snapshot,edges_snapshot,meta,"
                  "created_at,updated_at) VALUES (2,1,99,'running','[]','[]','{}',:t,:t)", p=99, t=_t(0))
        await run("INSERT INTO node_runs(id,workflow_run_id,node_key,node_type,status,progress,attempts,error,meta,"
                  "created_at,updated_at,finished_at) VALUES (1,1,'n1','script','failed',0,2,'сбой модели',"
                  "'{}',:t,:t,:t)", t=_t(1))
        await run("INSERT INTO node_runs(id,workflow_run_id,node_key,node_type,status,progress,attempts,meta,"
                  "created_at,updated_at) VALUES (2,2,'n1','script','done',0,1,'{}',:t,:t)", t=_t(1))
        await run("INSERT INTO observation_events(id,event_id,execution_id,project_id,workflow_run_id,node_key,"
                  "request_id,db_scope,kind,payload,created_at) VALUES (1,'e1','ex1',:p,1,'n1','req1','master',"
                  "'media_failure',:pl,:t)", p=P, pl=json.dumps({"api_key": "sk-ABCDEF1234567890XYZ", "x": 1}),
                  t=_t(2))
        await run("INSERT INTO observation_events(id,event_id,execution_id,project_id,workflow_run_id,db_scope,"
                  "kind,payload,created_at) VALUES (2,'e2','ex1',99,2,'master','чужое',:pl,:t)", pl="{}", t=_t(2))
        await run("INSERT INTO observation_incidents(incident_id,execution_id,project_id,node_key,type,severity,"
                  "status,detected_at,updated_at,details) VALUES ('inc1','ex1',:p,'n1','api_error','error','open',"
                  ":t,:t,'{\"why\":\"429\"}')", p=P, t=_t(3))
        await run("INSERT INTO observation_degradations(gap_id,execution_id,project_id,stage,error_type,"
                  "recoverable,attempts,recorded_at,last_error_at) VALUES ('g1','ex1',:p,'outbox','OSError',1,1,:t,:t)",
                  p=P, t=_t(4))
        await run("INSERT INTO decision_records(decision_id,execution_id,project_id,incident_id,node_key,"
                  "source_attempt_id,context_version,status,model_steps,proposal,policy_result,verification,"
                  "created_at) VALUES ('d1','ex1',:p,'inc1','n1','a1','v','ready',0,'{\"act\":\"retry\"}','{}','{}',:t)",
                  p=P, t=_t(5))
        await run("INSERT INTO action_v1_records(action_id,execution_id,project_id,action_kind,node_key,initiator,"
                  "rationale,evidence_ids,preconditions,idempotency_key,status,verification,created_at) VALUES "
                  "('a1','ex1',:p,'retry','n1','op','повтор после сбоя','[]','{}','k1','accepted','{}',:t)",
                  p=P, t=_t(6))
        await run("INSERT INTO action_layer_history(created_at,plan_id,event,actor,request_text,action_id,outcome,"
                  "error,project_id,request_id) VALUES (:t,'pl1','executed','op','сделай','a',"
                  "'ok','',:p,'req2')", p=P, t=_t(7))
        await run("INSERT INTO action_layer_plan_context(plan_id,source,macro_id,project_id,context_version,"
                  "created_at) VALUES ('pl2','planner','',:p,'v',:t)", p=P, t=_t(7))
        await run("INSERT INTO action_layer_history(created_at,plan_id,event,actor,request_text,action_id,outcome,"
                  "error) VALUES (:t,'pl2','failed','op','',  'a','error','упало')", t=_t(8))
        await run("INSERT INTO spend_ledger(created_at,day,kind,provider,project_id,run_id,source,usd,credits,note,"
                  "request_id,ref) VALUES (:t,'2026-05-01','actual','kie',:p,'ex1','video',0.25,0,"
                  "'token=abcdef123456789012345678','req3','task9')", p=P, t=_t(9))
        await run("INSERT INTO spend_ledger(created_at,day,kind,provider,project_id,run_id,source,usd,credits,note,"
                  "request_id,ref) VALUES (:t,'2026-05-01','actual','kie',99,'',  'video',9,0,'','','')", t=_t(9))
        await run("INSERT INTO provider_task_ledger(created_at,project_id,key,task_id,provider,api,model,event,"
                  "status,note,run_id,request_id) VALUES (:t,:p,'k','task9','kie','v','m','poll','failed','',"
                  "'ex1','req3')", p=P, t=_t(10))
        await run("INSERT INTO operator_audit(ts,request_id,method,path,route_template,project_id,actor,status_code,"
                  "duration_ms,outcome) VALUES (:t,'req4','POST','/api/projects/7/go','/api/projects/{id}/go',:p,"
                  "'local',200,5,'ok')", p=P, t=_t(11))
        await run("INSERT INTO publications(project_id,platform,sha256,status,attempts,created_at,updated_at) "
                  "VALUES (:p,'youtube','abc123def456','failed',1,:e,:e)", p=P,
                  e=(T0 + timedelta(minutes=12)).replace(tzinfo=__import__('datetime').timezone.utc).timestamp())
        await run("INSERT INTO observation_commit_uncertainty(execution_id,project_id,reason,recorded_at) "
                  "VALUES ('ex1',:p,'commit_unconfirmed',:t)", p=P, t=_t(13))
        await run("INSERT INTO notification_log(ts,project_id,run_id,channel,kind,text_redacted,ok,error) "
                  "VALUES (:t,:p,'ex1','telegram','hitl_request','Проверьте кадр',1,NULL)", p=P, t=_t(14))
        await run("INSERT INTO notification_log(ts,project_id,run_id,channel,kind,text_redacted,ok,error) "
                  "VALUES (:t,:p,'ex1','telegram_off','step_done','Шаг готов',0,'Telegram отключён')",
                  p=P, t=_t(15))
        await s.commit()


@pytest.fixture
async def db(tmp_path):
    eng, sm = await _make_db(tmp_path)
    await _seed(sm)
    yield sm
    await eng.dispose()


def _app(sm, monkeypatch):
    from app.web.routers import observations

    class _Obs:
        master = staticmethod(lambda: sm())

    monkeypatch.setattr(observations, "get_observer", lambda: _Obs())
    app = FastAPI()
    from app.web.auth_sessions import require_web_user
    app.dependency_overrides[require_web_user] = lambda: None
    app.include_router(observations.timeline_router, prefix="/api")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://localhost")


async def _all(sm, **kw):
    async with sm() as s:
        return await tl.timeline(s, P, limit=500, **kw)


async def test_all_sources_merged_in_order(db):
    res = await _all(db)
    ids = [i["id"] for i in res["items"]]
    srcs = {i["source"] for i in res["items"]}
    assert srcs == {"node_runs", "observation_events", "observation_incidents", "observation_degradations",
                    "decision_records", "action_v1_records", "action_layer_history", "spend_ledger",
                    "provider_task_ledger", "operator_audit", "publications",
                    "observation_commit_uncertainty", "notification_log"}
    assert len(srcs) == 13 and res["sources_missing"] == []
    ts = [i["ts"] for i in res["items"]]
    assert ts == sorted(ts, reverse=True)
    assert len(ids) == len(set(ids)) == 15  # 13 источников; слой действий 2 строки (напрямую и через план), уведомления 2
    assert all(i["project_id"] == P for i in res["items"])
    # чужой проект не виден
    assert not any("чужое" in i["title_ru"] for i in res["items"])
    assert all(i["amount_usd"] != 9 for i in res["items"])
    assert res["next_cursor"] is None and res["total_hint"] is None
    keys = {"id", "ts", "kind", "source", "severity", "title_ru", "detail_ru", "project_id", "run_id",
            "request_id", "ref", "amount_usd"}
    assert all(set(i) == keys for i in res["items"])
    assert all(i["severity"] in ("info", "warn", "error") for i in res["items"])
    by = {i["source"]: i for i in res["items"] if i["source"] != "action_layer_history"}
    assert by["spend_ledger"]["amount_usd"] == 0.25 and by["spend_ledger"]["ref"] == "task9"
    assert by["provider_task_ledger"]["severity"] == "error"
    assert by["publications"]["ts"].startswith("2026-05-01T12:12")
    assert by["node_runs"]["severity"] == "error"
    assert by["observation_incidents"]["title_ru"].startswith("Зафиксирована ошибка API")


async def test_layer_history_scoped_via_plan_context(db):
    res = await _all(db, kinds="action_layer_history")
    assert {i["ref"] for i in res["items"]} == {"pl1", "pl2"}


async def test_secrets_are_redacted(db):
    res = await _all(db)
    blob = json.dumps(res, ensure_ascii=False)
    assert "sk-ABCDEF1234567890XYZ" not in blob
    assert "abcdef123456789012345678" not in blob


async def test_filters_and_search(db):
    assert {i["source"] for i in (await _all(db, severity="error"))["items"]} >= {"node_runs", "publications"}
    assert all(i["severity"] == "error" for i in (await _all(db, severity="error"))["items"])
    spend = await _all(db, kinds="spend")
    assert [i["source"] for i in spend["items"]] == ["spend_ledger"]
    by_source = await _all(db, kinds="operator_audit,publication")
    assert {i["source"] for i in by_source["items"]} == {"operator_audit", "publications"}
    q = await _all(db, q="ПОВТОР после")
    assert [i["source"] for i in q["items"]] == ["action_v1_records"]
    rng = await _all(db, since=_t(5).replace(" ", "T") + "Z", until=_t(7).replace(" ", "T") + "Z")
    assert {i["source"] for i in rng["items"]} == {"decision_records", "action_v1_records", "action_layer_history"}
    assert (await _all(db, q="нет-такого-текста"))["items"] == []


async def test_cursor_pages_cover_everything_without_duplicates(db):
    full = [i["id"] for i in (await _all(db))["items"]]
    for size in (1, 2, 5):
        got, cur = [], None
        for _ in range(40):
            async with db() as s:
                page = await tl.timeline(s, P, limit=size, cursor=cur)
            got += [i["id"] for i in page["items"]]
            cur = page["next_cursor"]
            if cur is None:
                break
        assert got == full, size


async def test_ties_with_equal_timestamp_are_stable(db):
    async with db() as s:
        for n in range(4):
            await s.execute(text("INSERT INTO operator_audit(ts,request_id,method,path,project_id,actor,status_code,"
                                 "duration_ms,outcome) VALUES (:t,:r,'POST','/x',:p,'a',200,1,'ok')"),
                            {"t": _t(30), "r": f"r{n}", "p": P})
        await s.commit()
    full = [i["id"] for i in (await _all(db))["items"]]
    got, cur = [], None
    while True:
        async with db() as s:
            page = await tl.timeline(s, P, limit=3, cursor=cur)
        got += [i["id"] for i in page["items"]]
        cur = page["next_cursor"]
        if cur is None:
            break
    assert got == full and len(got) == len(set(got)) == 19


async def test_missing_source_goes_to_sources_missing_not_error(tmp_path):
    eng, sm = await _make_db(tmp_path, with_notifications=False)
    async with sm() as s:
        await s.execute(text("DROP TABLE publications"))
        await s.execute(text("DROP TABLE decision_records"))
        await s.commit()
    res = await _all(sm)
    assert {"publications", "decision_records", "notification_log"} <= set(res["sources_missing"])
    assert res["items"] == []
    await eng.dispose()


async def test_old_schema_without_correlation_columns(tmp_path):
    """spend_ledger / provider_task_ledger / action_layer_history без колонок B02 читаются."""
    eng, sm = await _make_db(tmp_path)
    async with sm() as s:
        await s.execute(text("DROP TABLE spend_ledger"))
        await s.execute(text("CREATE TABLE spend_ledger(id INTEGER PRIMARY KEY, created_at DATETIME, day TEXT, "
                             "kind TEXT, provider TEXT, project_id INTEGER, run_id TEXT, source TEXT, usd REAL, "
                             "credits REAL, note TEXT)"))
        await s.execute(text("INSERT INTO spend_ledger VALUES (1,:t,'d','actual','kie',:p,'r','s',1.5,0,'')"),
                        {"t": _t(1), "p": P})
        await s.commit()
    res = await _all(sm)
    assert [i["amount_usd"] for i in res["items"]] == [1.5]
    assert res["items"][0]["request_id"] is None
    await eng.dispose()


async def test_no_side_effects(db, tmp_path):
    path = tmp_path / "state.db"

    def snap():
        c = sqlite3.connect(path)
        try:
            tables = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")]
            return {t: c.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}, \
                c.execute("PRAGMA schema_version").fetchone()[0]
        finally:
            c.close()

    before = snap()
    await _all(db)
    async with db() as s:
        await tl.export(s, P, "csv")
        await tl.export(s, P, "json", q="сбой")
    assert snap() == before


async def test_describe_hook_with_fallback(db, monkeypatch):
    calls = []

    class _Ru:
        @staticmethod
        def describe(row):
            source = row["source_table"]
            calls.append(source)
            if source == "spend_ledger":
                return ("Трата (из timeline_ru)", "d", "warn")
            if source == "node_runs":
                raise RuntimeError("boom")
            return None

    monkeypatch.setattr(tl, "_ru", _Ru)
    by = {i["source"]: i for i in (await _all(db))["items"]}
    assert by["spend_ledger"]["title_ru"] == "Трата (из timeline_ru)"
    assert by["spend_ledger"]["severity"] == "warn" and by["spend_ledger"]["detail_ru"] == "d"
    assert by["node_runs"]["title_ru"].startswith("Шаг «n1»")  # ошибка describe не роняет
    assert by["publications"]["title_ru"].startswith("Публикация")  # None -> запасной текст
    assert calls


async def test_validation_errors(db):
    async with db() as s:
        for kw in ({"severity": "fatal"}, {"since": "вчера"}, {"cursor": "###"}, {"limit": 0}, {"limit": 501}):
            with pytest.raises(tl.TimelineError):
                await tl.timeline(s, P, **kw)
        with pytest.raises(tl.TimelineError):
            await tl.export(s, P, "xml")


async def test_http_totals_cover_whole_project(db, monkeypatch):
    """B06: итоги вкладки считаются по всему проекту, а не по странице; деньги, диск и счётчики на месте."""
    async with db() as s:
        want_err = len((await tl.timeline(s, P, severity="error", limit=500))["items"])
        want_warn = len((await tl.timeline(s, P, severity="warn", limit=500))["items"])
        want_gap = len((await tl.timeline(s, P, kinds="gap", limit=500))["items"])
    async with _app(db, monkeypatch) as c:
        r = await c.get(f"/api/projects/{P}/timeline/totals")
        assert r.status_code == 200
        body = r.json()
        assert body["counts"]["errors"] == {"count": want_err, "more": False}
        assert body["counts"]["warnings"] == {"count": want_warn, "more": False}
        assert body["counts"]["gaps"] == {"count": want_gap, "more": False}
        assert {"daily_spent", "project_spent", "run_spent", "run_id", "caps"} <= set(body["spend"])
        assert body["disk"]["level"] in ("ok", "low", "critical", "unknown")
        assert body["disk"]["free_gb"] is None or body["disk"]["free_gb"] >= 0
        assert (await c.post(f"/api/projects/{P}/timeline/totals")).status_code == 405


async def test_http_timeline_and_export(db, monkeypatch):
    async with _app(db, monkeypatch) as c:
        r = await c.get(f"/api/projects/{P}/timeline", params={"limit": 5})
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 5 and body["next_cursor"]
        r2 = await c.get(f"/api/projects/{P}/timeline", params={"limit": 500, "cursor": body["next_cursor"]})
        assert len(body["items"]) + len(r2.json()["items"]) == 15
        assert (await c.get(f"/api/projects/{P}/timeline", params={"limit": 501})).status_code == 422
        assert (await c.get(f"/api/projects/{P}/timeline", params={"severity": "x"})).status_code == 422
        assert (await c.get(f"/api/projects/{P}/timeline", params={"cursor": "zz"})).status_code == 422
        # без токена оператора (raw_client), POST не существует
        assert (await c.post(f"/api/projects/{P}/timeline")).status_code == 405

        e = await c.get(f"/api/projects/{P}/timeline/export", params={"format": "csv", "kinds": "spend,task"})
        assert e.status_code == 200 and e.content.startswith(b"\xef\xbb\xbf")
        assert "attachment" in e.headers["content-disposition"]
        rows = list(csv.reader(io.StringIO(e.content.decode("utf-8-sig"))))
        assert tuple(rows[0]) == tl.EXPORT_COLUMNS and len(rows) == 3
        j = await c.get(f"/api/projects/{P}/timeline/export", params={"format": "json", "q": "kie"})
        data = j.json()
        assert data["truncated"] is False and data["items"] and data["sources_missing"] == []
        assert (await c.get(f"/api/projects/{P}/timeline/export", params={"format": "xml"})).status_code == 422


async def test_csv_formula_injection_guarded():
    out = tl.render_csv([{"id": "x:1", "title_ru": "=HYPERLINK(1)", "detail_ru": "+1", "amount_usd": -1.0}])
    rows = list(csv.reader(io.StringIO(out.decode("utf-8-sig"))))
    assert rows[1][5] == "'=HYPERLINK(1)" and rows[1][6] == "'+1"


async def test_export_row_cap_and_truncation(db, monkeypatch):
    monkeypatch.setattr(tl, "EXPORT_MAX_ROWS", 3)
    async with db() as s:
        body, media, ext = await tl.export(s, P, "json")
    d = json.loads(body)
    assert len(d["items"]) == 3 and d["truncated"] is True and ext == "json"


async def test_speed_on_many_rows(db):
    import time
    async with db() as s:
        await s.execute(text("INSERT INTO operator_audit(ts,request_id,method,path,project_id,actor,status_code,"
                             "duration_ms,outcome) SELECT :t, 'r'||value, 'POST','/x',:p,'a',200,1,'ok' FROM "
                             "(WITH RECURSIVE c(value) AS (SELECT 1 UNION ALL SELECT value+1 FROM c WHERE value<20000) "
                             "SELECT value FROM c)"), {"t": _t(-100), "p": P})
        await s.commit()
    t = time.perf_counter()
    async with db() as s:
        page = await tl.timeline(s, P, limit=100)
        deep = await tl.timeline(s, P, limit=100, q="r19999")
    assert len(page["items"]) == 100 and len(deep["items"]) >= 1
    assert time.perf_counter() - t < 15


# --- интеграция: все 13 источников -> русские тексты через timeline_ru.describe ----------------------------------
_RAW_KINDS = ("request_failed", "hitl_request", "step_done", "telegram_off", "api_error", "NodeRunStatus")


async def test_every_source_gives_russian_title_without_raw_kind(db):
    res = await _all(db)
    by_source: dict[str, list] = {}
    for it in res["items"]:
        by_source.setdefault(it["source"], []).append(it)
    assert len(by_source) == 13
    for name, items in by_source.items():
        assert any(i["title_ru"].strip() for i in items), name
        for i in items:
            assert any("а" <= ch.lower() <= "я" or ch in "ёЁ" for ch in i["title_ru"]), (name, i["title_ru"])
            assert i["severity"] in ("info", "warn", "error")
    blob = " ".join(i["title_ru"] + " " + i["detail_ru"] for i in res["items"])
    for raw in _RAW_KINDS:
        assert raw not in blob, raw
    off = next(i for i in by_source["notification_log"] if "Шаг" in i["title_ru"])
    assert off["severity"] == "warn" and "не доставлено" in off["title_ru"]
    ok = next(i for i in by_source["notification_log"] if "подтверждения" in i["title_ru"])
    assert ok["severity"] == "info" and "доставлено" in ok["title_ru"] and "не доставлено" not in ok["title_ru"]


async def test_http_timeline_filters_and_export_russian(db, monkeypatch):
    async with _app(db, monkeypatch) as c:
        r = await c.get(f"/api/projects/{P}/timeline", params={"limit": 500})
        assert r.status_code == 200
        body = r.json()
        assert len(body["items"]) == 15 and body["sources_missing"] == []
        assert all(i["title_ru"] and i["title_ru"] != i["kind"] for i in body["items"])
        warn = (await c.get(f"/api/projects/{P}/timeline", params={"severity": "warn"})).json()["items"]
        assert warn and all(i["severity"] == "warn" for i in warn)
        notif = (await c.get(f"/api/projects/{P}/timeline", params={"kinds": "notification_log"})).json()["items"]
        assert len(notif) == 2 and {i["source"] for i in notif} == {"notification_log"}
        found = (await c.get(f"/api/projects/{P}/timeline", params={"q": "Telegram"})).json()["items"]
        assert found
        e = await c.get(f"/api/projects/{P}/timeline/export", params={"format": "csv"})
        rows = list(csv.reader(io.StringIO(e.content.decode("utf-8-sig"))))
        assert len(rows) == 16 and tuple(rows[0]) == tl.EXPORT_COLUMNS
        assert all(r_[tl.EXPORT_COLUMNS.index("title_ru")].strip() for r_ in rows[1:])
        j = (await c.get(f"/api/projects/{P}/timeline/export", params={"format": "json"})).json()
        assert len(j["items"]) == 15 and j["truncated"] is False


async def test_layer_history_scoped_via_plan_project_without_context(tmp_path):
    """План из /plans/preview: ни контекста, ни project_id в истории, но проект записан в самом плане (D-3)."""
    eng, sm = await _make_db(tmp_path)
    try:
        async with sm() as s:
            await s.execute(text(
                "INSERT INTO action_layer_plans(id,project_id,operator,request_text,plan_json,plan_hash,risk_max,"
                "needs_approval,status,cost_low,cost_high,cost_unknown_steps,stop_requested,stop_reason,error,created_at,updated_at) VALUES "
                "('plX',:p,'op','правка','{}','h','safe',0,'done',0,0,0,0,'','',:t,:t)"), {"p": P, "t": _t(1)})
            await s.execute(text(
                "INSERT INTO action_layer_plans(id,project_id,operator,request_text,plan_json,plan_hash,risk_max,"
                "needs_approval,status,cost_low,cost_high,cost_unknown_steps,stop_requested,stop_reason,error,created_at,updated_at) VALUES "
                "('plY',99,'op','чужой','{}','h','safe',0,'done',0,0,0,0,'','',:t,:t)"), {"t": _t(1)})
            for plan in ("plX", "plY"):
                await s.execute(text(
                    "INSERT INTO action_layer_history(created_at,plan_id,event,actor,request_text,action_id,outcome,"
                    "error) VALUES (:t,:pl,'executed','op','','a','ok','')"), {"t": _t(2), "pl": plan})
            await s.commit()
        async with sm() as s:
            page = await tl.timeline(s, P, limit=50)
        layer = [i for i in page["items"] if i.get("kind") == "action" and "executed" in (i.get("title_ru") or "")]
        assert len(layer) == 1  # план своего проекта виден, план проекта 99 - нет
    finally:
        await eng.dispose()
