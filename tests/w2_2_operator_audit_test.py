from __future__ import annotations

import hashlib
import json
import re
import sqlite3

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from loguru import logger
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.services import operator_audit as oa
from app.services import schema_migrations as sm
from app.settings import settings

pytestmark = pytest.mark.raw_client
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("VP_OPERATOR_TOKEN_ENFORCE", "1")
    monkeypatch.delenv("VP_OPERATOR_AUDIT", raising=False)
    monkeypatch.delenv("VP_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("VP_ALLOWED_HOSTS", raising=False)
    monkeypatch.setattr(settings, "web_host", "127.0.0.1")
    monkeypatch.setattr(settings, "web_port", 8765)
    oa.reset_stats()
    oa._ensured.clear()


def _client(app, base="http://localhost:8765") -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app, client=("127.0.0.1", 5000), raise_app_exceptions=False),
                       base_url=base)


def _stub_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(oa.OperatorAuditMiddleware)

    @app.get("/api/things")
    async def list_things():
        return {"ok": True}

    @app.post("/api/projects/{project_id}/go")
    async def go(project_id: int, request: Request):
        logger.info("inside handler")
        return {"id": project_id, "rid": request.state.request_id}

    @app.post("/api/things")
    async def create(payload: dict):
        return payload

    @app.put("/api/things/{thing_id}")
    async def put_thing(thing_id: str):
        return {}

    @app.delete("/api/things/{thing_id}")
    async def del_thing(thing_id: str):
        return {}

    @app.post("/api/auth/login")
    async def login(payload: dict):
        return {}

    @app.post("/api/projects/{project_id}/ensure-run")
    async def ensure_run(project_id: int):
        return {"run_id": 1}

    @app.post("/api/boom")
    async def boom():
        raise RuntimeError("boom")

    @app.post("/other/not-api")
    async def not_api():
        return {}

    return app


def _rows(db_path=None) -> list[dict]:
    return oa.list_rows(limit=1000)



async def test_mutating_methods_write_and_get_does_not():
    async with _client(_stub_app()) as c:
        assert (await c.get("/api/things")).status_code == 200
        assert (await c.post("/api/things", json={"a": 1})).status_code == 200
        assert (await c.put("/api/things/7", json={})).status_code == 200
        assert (await c.delete("/api/things/7")).status_code == 200
        assert (await c.post("/other/not-api")).status_code == 200
        assert (await c.options("/api/things")).status_code in (200, 405)
    rows = _rows()
    assert sorted(r["method"] for r in rows) == ["DELETE", "POST", "PUT"]
    assert all(r["path"].startswith("/api/") for r in rows)


async def test_automatic_ensure_run_is_not_an_operator_action():
    async with _client(_stub_app()) as c:
        assert (await c.post("/api/projects/4/ensure-run")).status_code == 200
        assert (await c.post("/api/projects/4/go")).status_code == 200
    paths = [r["path"] for r in _rows()]
    assert not any(p.endswith("/ensure-run") for p in paths)
    assert any(p.endswith("/go") for p in paths)


def test_failed_ensure_run_is_still_recorded():
    assert oa.is_automatic_ok("/api/projects/4/ensure-run", 200)
    assert not oa.is_automatic_ok("/api/projects/4/ensure-run", 403)
    assert not oa.is_automatic_ok("/api/projects/4/ensure-run", 500)
    assert not oa.is_automatic_ok("/api/projects/4/go", 200)


async def test_row_fields_route_project_actor_outcome():
    async with _client(_stub_app()) as c:
        r = await c.post("/api/projects/42/go", json={"x": 1})
        rid = r.headers["X-Request-ID"]
        assert r.json()["rid"] == rid
        bad = await c.post("/api/projects/abc/go")
        assert bad.status_code == 422
    rows = {r["request_id"]: r for r in _rows()}
    row = rows[rid]
    assert row["route_template"] == "/api/projects/{project_id}/go"
    assert row["project_id"] == 42 and row["status_code"] == 200 and row["outcome"] == "ok"
    assert row["actor"] == "local" and row["duration_ms"] >= 0 and row["ts"]
    assert any(r["outcome"] == "rejected" and r["status_code"] == 422 for r in rows.values())


async def test_project_id_from_json_body_and_operator_actor():
    from app.services import access_guard as ag
    hdr = {ag.OPERATOR_HEADER: ag.get_operator_token()}
    async with _client(_stub_app()) as c:
        await c.post("/api/things", json={"project_id": 9, "a": 1}, headers=hdr)
        await c.post("/api/things", json={"project_id": True})
    by_actor = {r["actor"]: r for r in _rows()}
    assert by_actor["operator"]["project_id"] == 9
    assert by_actor["local"]["project_id"] is None
    assert all(ag.get_operator_token() not in json.dumps(r) for r in _rows())


async def test_exception_is_error_outcome_and_500():
    async with _client(_stub_app()) as c:
        r = await c.post("/api/boom")
    assert r.status_code == 500
    (row,) = _rows()
    assert row["outcome"] == "error" and row["status_code"] == 500



async def test_bodies_and_secrets_are_not_stored():
    secret = "hunter2-very-secret-password"
    token = "Zq" * 10 + "Xy9" * 8
    async with _client(_stub_app()) as c:
        await c.post("/api/things", json={"password": secret, "note": "hello", "api_key": "sk-abcdef123456"})
        await c.put(f"/api/things/{token}?api_key=sk-zzzzzzzzzz", json={})
        await c.post("/api/auth/login", json={"username": "u", "password": secret})
    rows = {r["method"] + r["path"]: r for r in _rows()}
    flat = json.dumps(list(rows.values()))
    assert secret not in flat and "sk-abcdef" not in flat and token not in flat and "sk-zzzz" not in flat
    assert "[redacted]" in rows["PUT/api/things/[redacted]"]["path"]
    raw = oa._db_path().read_bytes()
    assert secret.encode() not in raw and b"hello" not in raw
    from app.services.observation import digest
    expected = digest({"password": "x", "note": "hello", "api_key": "y"})
    assert rows["POST/api/things"]["body_sha256"] == expected
    assert rows["POST/api/auth/login"]["body_sha256"] is None


async def test_non_json_body_digest_is_raw_sha256_and_empty_is_null():
    payload = b"\x00\x01binary" * 10
    async with _client(_stub_app()) as c:
        await c.post("/api/things", content=payload, headers={"Content-Type": "application/octet-stream"})
        await c.delete("/api/things/1")
    rows = {r["method"]: r for r in _rows()}
    assert rows["POST"]["body_sha256"] == hashlib.sha256(payload).hexdigest()
    assert rows["DELETE"]["body_sha256"] is None


def test_redact_path_keeps_ids_and_uuid_and_sha():
    u = "123e4567-e89b-12d3-a456-426614174000"
    h = "a" * 64
    assert oa.redact_path(f"/api/artifacts/{u}/{h}/7") == f"/api/artifacts/{u}/{h}/7"
    assert oa.redact_path("/api/x?token=zzz") == "/api/x"



async def test_request_id_header_and_log_context():
    seen: list[str | None] = []
    sink = logger.add(lambda m: seen.append(m.record["extra"].get("request_id")), level="INFO")
    try:
        async with _client(_stub_app()) as c:
            r = await c.post("/api/projects/1/go")
            r2 = await c.post("/api/projects/1/go", headers={"X-Request-ID": "client-req-0001"})
            r3 = await c.post("/api/projects/1/go", headers={"X-Request-ID": "bad id!"})
    finally:
        logger.remove(sink)
    assert r.headers["X-Request-ID"] in seen
    assert r2.headers["X-Request-ID"] == "client-req-0001" and "client-req-0001" in seen
    assert r3.headers["X-Request-ID"] != "bad id!" and re.fullmatch(r"[0-9a-f]{32}", r3.headers["X-Request-ID"])


async def test_get_also_gets_request_id_but_no_row():
    async with _client(_stub_app()) as c:
        r = await c.get("/api/things")
    assert r.headers.get("X-Request-ID") and _rows() == []



async def test_write_failure_does_not_block_and_is_counted(monkeypatch):
    def _broken(row):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(oa, "insert_row", _broken)
    errors: list[str] = []
    sink = logger.add(lambda m: errors.append(m.record["message"]), level="ERROR")
    try:
        async with _client(_stub_app()) as c:
            r = await c.post("/api/things", json={"a": 1})
            await c.post("/api/things", json={"a": 2})
    finally:
        logger.remove(sink)
    assert r.status_code == 200 and r.json() == {"a": 1}
    assert oa.skipped_writes() == 2 and "disk I/O" in oa.stats()["last_error"]
    assert errors and "аудита пропущена" in errors[0]


async def test_kill_switch_env_disables_rows(monkeypatch):
    monkeypatch.setenv("VP_OPERATOR_AUDIT", "0")
    async with _client(_stub_app()) as c:
        r = await c.post("/api/things", json={})
    assert r.status_code == 200 and _rows() == []



async def test_table_rejects_update_and_delete():
    async with _client(_stub_app()) as c:
        await c.post("/api/things", json={})
    con = sqlite3.connect(str(oa._db_path()))
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("UPDATE operator_audit SET actor='x'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("DELETE FROM operator_audit")
        assert con.execute("SELECT count(*) FROM operator_audit").fetchone()[0] == 1
    finally:
        con.close()



@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    yield eng
    await eng.dispose()


def test_0011_0012_registered_in_order():
    ids = [m.id for m in sm.MIGRATIONS]
    assert ids[-7:-5] == ["0011_operator_audit", "0012_publications"] and ids == sorted(ids)
    assert ids[-5:-2] == ["0013_notification_log", "0014_journal_archive_log", "0015_create_jobs"]
    assert ids[-2:] == ["0016_outsee_idempotency", "0017_publish_approvals"]


async def test_migrations_create_tables_and_triggers_idempotently(engine):
    from app.services import publications_store as ps
    async with engine.begin() as conn:
        for ddl in ps._DDL:
            await conn.exec_driver_sql(ddl)
        await conn.exec_driver_sql(
            "INSERT INTO publications (project_id, platform, sha256, status, created_at, updated_at) "
            "VALUES (1, 'yt', 'h', 'planned', 1, 1)")
    async with engine.begin() as conn:
        report = await sm.run_migrations(conn)
    assert report.failed is None, report.error
    assert {"0011_operator_audit", "0012_publications"} <= set(report.applied)
    async with engine.begin() as conn:
        again = await sm.run_migrations(conn)
    assert again.failed is None and not again.applied and not again.drift
    async with engine.connect() as conn:
        tables = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
        cols = {c["name"] for c in await conn.run_sync(lambda c: inspect(c).get_columns("operator_audit"))}
        n = (await conn.exec_driver_sql("SELECT count(*) FROM publications")).scalar()
        trg = {r[0] for r in (await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='trigger'")).fetchall()}
    assert {"operator_audit", "publications"} <= tables and n == 1
    assert cols == {"id", "ts", "request_id", "method", "path", "route_template", "project_id", "actor",
                    "status_code", "duration_ms", "body_sha256", "outcome"}
    assert {"operator_audit_no_update", "operator_audit_no_delete"} <= trg


async def test_migration_creates_publications_on_empty_db(engine):
    async with engine.begin() as conn:
        report = await sm.run_migrations(conn)
    assert report.failed is None, report.error
    async with engine.connect() as conn:
        idx = {r[0] for r in (await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index'")).fetchall()}
    assert "ux_publications_idem" in idx


async def test_runtime_ddl_matches_migration_table(engine):
    async with engine.begin() as conn:
        await sm.run_migrations(conn)
        mig = [(r[1], r[2], r[3]) for r in (await conn.exec_driver_sql("PRAGMA table_info(operator_audit)")).fetchall()]
    oa.insert_row({"ts": "2026-01-01 00:00:00", "request_id": "r" * 8, "method": "POST", "path": "/api/x",
                   "route_template": None, "project_id": None, "actor": "local", "status_code": 200,
                   "duration_ms": 1, "body_sha256": None, "outcome": "ok"})
    con = sqlite3.connect(str(oa._db_path()))
    try:
        rt = [(r[1], r[2], r[3]) for r in con.execute("PRAGMA table_info(operator_audit)")]
    finally:
        con.close()
    assert rt == mig



def _mutating_routes(app) -> list[tuple[str, str]]:
    from fastapi.routing import APIRoute
    out = set()

    def add(methods, path):
        if path.startswith("/api"):
            for m in set(methods or ()) & MUTATING:
                out.add((m, re.sub(r"\{[^}]+\}", "1", path)))

    for route in app.routes:
        if isinstance(route, APIRoute):
            add(route.methods, route.path)
        elif hasattr(route, "effective_route_contexts"):
            for ctx in route.effective_route_contexts():
                add(getattr(ctx.original_route, "methods", None), ctx.path)
    return sorted(out)


async def test_every_real_mutating_route_leaves_a_row_and_get_does_not():
    from app.web.api import create_app
    app = create_app()
    ops = _mutating_routes(app)
    assert len(ops) >= 150, len(ops)
    schema = {(m.upper(), re.sub(r"\{[^}]+\}", "1", p)) for p, item in app.openapi()["paths"].items()
              if p.startswith("/api") for m in item if m.upper() in MUTATING}
    assert set(ops) == schema
    rids: dict[str, tuple[str, str]] = {}
    async with _client(app) as c:
        for method, url in ops:
            r = await c.request(method, url, headers={"Host": "evil.example.com"})
            assert r.status_code == 403, (method, url)
            rid = r.headers["X-Request-ID"]
            rids[rid] = (method, url)
        g = await c.get("/api/openapi.json")
        assert g.status_code == 200 and g.headers.get("X-Request-ID")
    rows = {r["request_id"]: r for r in oa.list_rows(limit=10000)}
    assert set(rows) == set(rids)
    for rid, (method, url) in rids.items():
        row = rows[rid]
        assert (row["method"], row["path"]) == (method, url), rid
        assert row["outcome"] == "denied" and row["status_code"] == 403
    assert oa.skipped_writes() == 0


async def test_route_template_has_prefix_for_included_router_and_none_for_404():
    from fastapi import APIRouter
    app = FastAPI()
    app.add_middleware(oa.OperatorAuditMiddleware)
    router = APIRouter()

    @router.post("/{project_id}/steps/{step}/run")
    async def run(project_id: int, step: str):
        return {}

    @router.post("/files/{rest:path}")
    async def files(rest: str):
        return {}

    app.include_router(router, prefix="/api/projects")
    async with _client(app) as c:
        a = await c.post("/api/projects/5/steps/make_plan/run")
        b = await c.post("/api/projects/files/a/b/c.png")
        n = await c.post("/api/nothing/here")
    rows = {r["request_id"]: r for r in oa.list_rows()}
    ra, rb, rn = (rows[x.headers["X-Request-ID"]] for x in (a, b, n))
    assert ra["route_template"] == "/api/projects/{project_id}/steps/{step}/run" and ra["project_id"] == 5
    assert rb["route_template"] == "/api/projects/files/{rest}"
    assert rn["route_template"] is None and rn["status_code"] == 404 and rn["outcome"] == "rejected"
