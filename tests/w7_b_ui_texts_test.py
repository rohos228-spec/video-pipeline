"""W7-B: правки по визуальной проверке Studio (docs/UI_CHECK.md): тексты журнала и публикаций, общая ручка HITL.

Без сети, браузера и площадок. Рабочая папка и БД вне репозитория (tests/_stand_workdir.py через Env).
Класс доказательства: U (тесты на заменителях). Страницу в браузере проверяет scripts/ui_check_journal_publications.mjs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.models import HITLDecision, HITLKind, HITLRequest
from app.services import publications_store as ps
from app.services import timeline as tl
from app.services import timeline_ru as tr
from app.web import deps
from app.web.routers import hitl as hitl_router
from app.web.routers import publications as pub_router
from tests.w6_1_support import Env, make_project

ROOT = Path(__file__).resolve().parents[1]
RU_HITL_REFUSAL = "Решение по публикации принимается только на странице публикаций или кнопкой в Telegram"
CYR = re.compile("[а-яёА-ЯЁ]")


@pytest.fixture
async def env(monkeypatch):
    e = await Env().create()
    # настоящий путь HITL: master-база стенда и папка данных подставлены на уровне модулей, а не
    # переопределением зависимости get_session в приложении
    import app.db as app_db
    from app.settings import settings
    monkeypatch.setattr(app_db, "SessionLocal", e.maker)
    monkeypatch.setattr(deps, "SessionLocal", e.maker)
    monkeypatch.setattr(settings, "data_dir", e.workdir)
    yield e
    await e.close()


# --- общая ручка HITL не меняет approve_publish ---------------------------------------------------------------------

def _hitl_app(env: Env) -> FastAPI:
    app = FastAPI()
    app.include_router(hitl_router.router, prefix="/api")
    assert not app.dependency_overrides  # без подмены сессии
    return app


async def _make_request(env: Env, kind: HITLKind, decision=HITLDecision.pending) -> int:
    async with env.maker() as db:
        p = await make_project(db, env.workdir, slug=f"h-{kind.value}-{decision.value}")
        req = HITLRequest(project_id=p.id, kind=kind, payload={"platform": "tiktok"}, decision=decision)
        db.add(req)
        await db.commit()
        return req.id


@pytest.mark.parametrize("decision", ["approve", "approved", "reject", "regenerate", "edit_prompt", "что-угодно"])
async def test_generic_decision_refuses_approve_publish(env, decision):
    rid = await _make_request(env, HITLKind.approve_publish)
    async with AsyncClient(transport=ASGITransport(app=_hitl_app(env)), base_url="http://t") as c:
        r = await c.post(f"/api/hitl/{rid}/decision", json={"decision": decision})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == RU_HITL_REFUSAL
    async with env.maker() as db:
        req = (await db.execute(select(HITLRequest).where(HITLRequest.id == rid))).scalar_one()
        assert req.decision is HITLDecision.pending and req.decided_at is None  # запрос не тронут


async def test_generic_decision_refuses_even_already_decided_publish_request(env):
    rid = await _make_request(env, HITLKind.approve_publish, HITLDecision.approved)
    async with AsyncClient(transport=ASGITransport(app=_hitl_app(env)), base_url="http://t") as c:
        r = await c.post(f"/api/hitl/{rid}/decision", json={"decision": "reject"})
    assert r.status_code == 409 and r.json()["detail"] == RU_HITL_REFUSAL
    async with env.maker() as db:
        req = (await db.execute(select(HITLRequest).where(HITLRequest.id == rid))).scalar_one()
        assert req.decision is HITLDecision.approved


async def test_generic_decision_still_serves_other_kinds(env):
    """Запрет только для approve_publish: у остальных видов ручка работает как раньше (здесь - отказ по неверному решению)."""
    rid = await _make_request(env, HITLKind.approve_script)
    async with AsyncClient(transport=ASGITransport(app=_hitl_app(env)), base_url="http://t") as c:
        r = await c.post(f"/api/hitl/{rid}/decision", json={"decision": "нет-такого"})
    assert r.status_code == 400 and "Неизвестное решение" in r.json()["detail"]


# --- «Разрешить публикацию»: одно честное сообщение ------------------------------------------------------------------

@pytest.mark.parametrize("queued, enabled, must, must_not", [
    (True, True, "поставлено в очередь", "ничего не публикуется"),
    (False, False, "выключена настройкой", "поставлено в очередь"),
    (False, True, "учтёт ваше разрешение", "выключена настройкой"),
])
def test_approve_message_is_one_honest_statement(queued, enabled, must, must_not):
    msg = pub_router._approve_message(queued, enabled)
    assert must in msg and must_not not in msg
    assert msg.startswith("Разрешено") and CYR.search(msg)


# --- журнал: решение человека не ошибка, оператор с настоящим путём и кодом ответа ---------------------------------

def test_manual_not_published_is_a_human_decision_not_an_error():
    title, detail, sev = tr.describe({"source": "publications", "status": "failed", "platform": "ig_reels",
                                      "error_code": "manual_not_published"})
    assert sev == "info" and title.startswith("Закрыто человеком")
    assert "manual_not_published" not in title + detail and "ошибка" not in detail
    t2, d2, s2 = tr.describe({"source": "publications", "status": "confirmed", "platform": "yt_shorts",
                              "error_code": "manual_confirmed", "post_url": "https://x.invalid/1"})
    assert s2 == "info" and "человеком" in t2 and "manual_confirmed" not in t2 + d2
    # настоящий сбой остаётся ошибкой, с русской причиной вместо кода
    t3, d3, s3 = tr.describe({"source": "publications", "status": "failed", "platform": "tiktok",
                              "error_code": "not_logged_in"})
    assert s3 == "error" and "not_logged_in" not in t3 + d3 and "входа" in d3
    _, d4, _ = tr.describe({"source": "publications", "status": "skipped", "platform": "vk_clips",
                            "error_code": "publish_disabled"})
    assert "publish_disabled" not in d4 and "Автопубликация выключена" in d4


def test_operator_row_shows_real_path_and_response_code():
    t, d, s = tr.describe({"source": "operator_audit", "method": "POST", "outcome": "ok", "status_code": 200,
                           "path": "/api/projects/7/publications/yt_shorts/confirm-manual",
                           "route_template": "/api/projects/{project_id}/publications/{platform}/confirm-manual"})
    assert "/api/projects/7/publications/yt_shorts/confirm-manual" in d and "код ответа 200" in d
    assert "{project_id}" not in t + d and s == "info"
    # без пути остаётся шаблон (старые строки), но код ответа виден
    _, d2, _ = tr.describe({"source": "operator_audit", "method": "POST", "route_template": "/api/x/{id}",
                            "status_code": 409, "outcome": "rejected"})
    assert "код ответа 409" in d2


async def test_timeline_end_to_end_texts(tmp_path):
    from tests.w3_2_timeline_test import P, _make_db, _t
    eng, sm = await _make_db(tmp_path)
    try:
        async with sm() as s:
            async def run(sql, **kw):
                await s.execute(text(sql), kw)
            await run("INSERT INTO operator_audit(ts,request_id,method,path,route_template,project_id,actor,"
                      "status_code,duration_ms,outcome) VALUES (:t,'r1','POST',"
                      "'/api/projects/7/publications/ig_reels/mark-failed',"
                      "'/api/projects/{project_id}/publications/{platform}/mark-failed',:p,'operator',200,5,'ok')",
                      p=P, t=_t(1))
            await run("INSERT INTO publications(project_id,platform,sha256,status,attempts,error_code,created_at,"
                      "updated_at) VALUES (:p,'ig_reels','abc123def456','failed',0,'manual_not_published',:e,:e)",
                      p=P, e=1_780_000_000.0)
            await run("INSERT INTO observation_events(event_id,execution_id,project_id,workflow_run_id,node_key,"
                      "kind,payload,created_at,db_scope,request_id) VALUES ('e1','ex1',:p,1,'script','node_error',"
                      "'{}',:t,'master','req')", p=P, t=_t(2))
            await s.commit()
            res = await tl.timeline(s, P, limit=100)
        by = {i["source"]: i for i in res["items"]}
        assert by["publications"]["severity"] == "info" and by["publications"]["title_ru"].startswith("Закрыто человеком")
        op = by["operator_audit"]
        assert "{project_id}" not in json.dumps(op, ensure_ascii=False)
        assert "/api/projects/7/publications/ig_reels/mark-failed" in op["detail_ru"] and "код ответа 200" in op["detail_ru"]
        ev = by["observation_events"]
        assert CYR.search(ev["title_ru"]) and ev["title_ru"] != "node_error"
        assert not any(i["severity"] == "error" and i["source"] == "publications" for i in res["items"])
    finally:
        await eng.dispose()


# --- таблицы текстов на сервере и в Studio равны -------------------------------------------------------------------

def _ts_map(path: Path, name: str) -> dict[str, str]:
    src = path.read_text(encoding="utf-8")
    m = re.search(rf"export const {name}: Record<string, string> = \{{\n(.*?)\n\}};", src, re.S)
    assert m, name
    out = {}
    for line in m.group(1).splitlines():
        km = re.match(r'\s+([a-z_]+): (".*"),$', line)
        assert km, line
        out[km.group(1)] = json.loads(km.group(2))
    return out


def test_publication_code_table_equals_studio_table():
    ts = _ts_map(ROOT / "web" / "src" / "lib" / "publication-text.ts", "PUBLICATION_CODE_RU")
    assert ts == tr.PUBLICATION_CODE_RU
    from app.services.publish_accounts import REFUSALS
    assert set(REFUSALS) <= set(ts)  # любой отказ площадки имеет русскую фразу
    assert all(CYR.search(v) for v in ts.values())
    # коды, которые кладёт сам журнал публикаций
    for code in (ps.MANUAL_FAILED_CODE, "manual_confirmed", "publish_disabled", "confirm_timeout"):
        assert code in ts, code


def test_studio_kind_labels_have_no_raw_fallback():
    src = (ROOT / "web" / "src" / "lib" / "journal-view.ts").read_text(encoding="utf-8")
    assert "Событие «" not in src  # сырой вид в подписи больше не подставляется
    for kind in ("gap", "observation_events"):
        assert re.search(rf"\b{kind}: \"", src), kind
