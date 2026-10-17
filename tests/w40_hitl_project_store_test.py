from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.db as app_db
import app.web.deps as web_deps
from app.models import (
    Frame, FrameStatus, HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus,
)
from app.project_db import get_project_sessionmaker, init_project_db
from app.services import hitl_locator as hl
from app.settings import settings
from app.web.routers import hitl as hitl_router
from tests.w6_1_support import Env


@pytest.fixture
async def stores(monkeypatch):
    env = await Env().create()
    monkeypatch.setattr(app_db, "SessionLocal", env.maker)
    monkeypatch.setattr(web_deps, "SessionLocal", env.maker)
    monkeypatch.setattr(settings, "data_dir", env.workdir)
    yield env
    await env.close()


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(hitl_router.router, prefix="/api")
    assert not app.dependency_overrides
    return app


async def _project(env, slug: str, *, master_id: int | None = None) -> Project:
    async with env.maker() as db:
        p = Project(slug=slug, title="t", topic="тема", status=ProjectStatus.new, meta={})
        if master_id is not None:
            p.id = master_id
        db.add(p)
        await db.commit()
    await init_project_db(p.data_dir, p)
    return p


async def _local_add(project: Project, *rows):
    sessions = await get_project_sessionmaker(project.data_dir)
    async with sessions() as local:
        local.add_all(rows)
        await local.commit()


async def _local_get(project: Project, model, pk):
    sessions = await get_project_sessionmaker(project.data_dir)
    async with sessions() as local:
        return await local.get(model, pk)


async def _master_add(env, *rows):
    async with env.maker() as db:
        db.add_all(rows)
        await db.commit()


async def _post(hitl_id: int, body: dict, **params):
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        return await c.post(f"/api/hitl/{hitl_id}/decision", json=body, params=params)


def _req(project_id: int, rid: int, kind=HITLKind.approve_script, **kw) -> HITLRequest:
    return HITLRequest(id=rid, project_id=project_id, kind=kind, payload={}, **kw)


async def test_decision_of_project_request_lands_in_project_db_not_404(stores):
    p = await _project(stores, "d5-a")
    await _local_add(p, _req(p.id, 7))
    r = await _post(7, {"decision": "approve"})
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "approved"
    row = await _local_get(p, HITLRequest, 7)
    assert row.decision is HITLDecision.approved and row.decided_at is not None
    async with stores.maker() as db:
        assert await db.get(HITLRequest, 7) is None


async def test_author_of_decision_is_recorded(stores):
    p = await _project(stores, "d5-author")
    await _local_add(p, _req(p.id, 3))
    r = await _post(3, {"decision": "reject"})
    assert r.status_code == 200
    row = await _local_get(p, HITLRequest, 3)
    assert row.payload.get("decided_by", "").startswith("operator")


async def test_second_decision_is_refused_in_russian_and_changes_nothing(stores):
    p = await _project(stores, "d5-twice")
    await _local_add(p, _req(p.id, 5))
    first = await _post(5, {"decision": "approve"})
    assert first.status_code == 200
    decided_at = (await _local_get(p, HITLRequest, 5)).decided_at
    second = await _post(5, {"decision": "reject"})
    assert second.status_code == 400
    assert second.json()["detail"] == "Решение по этому запросу уже принято"
    row = await _local_get(p, HITLRequest, 5)
    assert row.decision is HITLDecision.approved and row.decided_at == decided_at


async def test_two_simultaneous_decisions_write_only_one(stores):
    p = await _project(stores, "d5-race")
    await _local_add(p, _req(p.id, 9))
    a, b = await asyncio.gather(_post(9, {"decision": "approve"}), _post(9, {"decision": "reject"}))
    assert sorted([a.status_code, b.status_code]) == [200, 400], (a.text, b.text)
    row = await _local_get(p, HITLRequest, 9)
    winner = a if a.status_code == 200 else b
    assert row.decision.value == winner.json()["decision"]


async def test_same_number_in_two_projects_is_ambiguous_until_project_is_named(stores):
    pa = await _project(stores, "d5-coll-a")
    pb = await _project(stores, "d5-coll-b")
    await _master_add(stores, _req(pa.id, 11))
    await _local_add(pb, _req(pb.id, 11))
    r = await _post(11, {"decision": "approve"})
    assert r.status_code == 409 and r.json()["detail"] == hl.AMBIGUOUS_RU
    assert (await _local_get(pb, HITLRequest, 11)).decision is HITLDecision.pending
    async with stores.maker() as db:
        assert (await db.get(HITLRequest, 11)).decision is HITLDecision.pending
    rb = await _post(11, {"decision": "approve", "project_id": pb.id})
    assert rb.status_code == 200
    assert (await _local_get(pb, HITLRequest, 11)).decision is HITLDecision.approved
    async with stores.maker() as db:
        assert (await db.get(HITLRequest, 11)).decision is HITLDecision.pending
    ra = await _post(11, {"decision": "reject"}, project_id=pa.id)
    assert ra.status_code == 200
    async with stores.maker() as db:
        assert (await db.get(HITLRequest, 11)).decision is HITLDecision.rejected


async def test_project_id_of_another_project_gives_404(stores):
    pa = await _project(stores, "d5-wrong-a")
    pb = await _project(stores, "d5-wrong-b")
    await _local_add(pa, _req(pa.id, 21))
    r = await _post(21, {"decision": "approve", "project_id": pb.id})
    assert r.status_code == 404


async def test_legacy_master_request_still_works(stores):
    p = await _project(stores, "d5-legacy")
    await _master_add(stores, _req(p.id, 31))
    r = await _post(31, {"decision": "approve"})
    assert r.status_code == 200
    async with stores.maker() as db:
        assert (await db.get(HITLRequest, 31)).decision is HITLDecision.approved


async def test_old_master_copy_is_mirrored_so_it_does_not_hang_pending(stores):
    p = await _project(stores, "d5-mirror")
    await _master_add(stores, _req(p.id, 41))
    await _local_add(p, _req(p.id, 41))
    r = await _post(41, {"decision": "reject"})
    assert r.status_code == 200
    assert (await _local_get(p, HITLRequest, 41)).decision is HITLDecision.rejected
    async with stores.maker() as db:
        assert (await db.get(HITLRequest, 41)).decision is HITLDecision.rejected


async def test_side_effects_are_written_to_project_db(stores):
    p = await _project(stores, "d5-effects")
    frame = Frame(id=1, project_id=p.id, number=1, voiceover_text="текст", status=FrameStatus.image_generated)
    await _local_add(p, frame, _req(p.id, 51, HITLKind.approve_images, frame_id=1))
    r = await _post(51, {"decision": "approve"})
    assert r.status_code == 200, r.text
    assert (await _local_get(p, Frame, 1)).status is FrameStatus.image_approved
    async with stores.maker() as db:
        assert await db.get(Frame, 1) is None


async def test_pending_list_collects_project_and_master_requests(stores):
    pa = await _project(stores, "d5-list-a")
    pb = await _project(stores, "d5-list-b")
    await _master_add(stores, _req(pa.id, 61))
    await _local_add(pb, _req(pb.id, 62), _req(pb.id, 63, decision=HITLDecision.approved))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://t") as c:
        r = await c.get("/api/hitl/pending")
    assert r.status_code == 200
    assert sorted(x["id"] for x in r.json()) == [61, 62]


async def test_old_telegram_button_format_still_answers_for_project_request(stores, monkeypatch):
    from app.telegram import bot

    p = await _project(stores, "d5-tg")
    await _local_add(p, _req(p.id, 71))
    answers = []

    async def answer(text="", show_alert=False, **kw):
        answers.append(text)

    monkeypatch.setattr(bot.settings, "telegram_owner_chat_id", 5)
    cb = SimpleNamespace(from_user=SimpleNamespace(id=5), data="hitl:71:approve", message=None, bot=None, answer=answer)
    await bot.on_hitl_callback(cb)
    assert answers and answers[-1].startswith("Решение"), answers
    assert (await _local_get(p, HITLRequest, 71)).decision is HITLDecision.approved
    await bot.on_hitl_callback(cb)
    assert "обработан" in answers[-1].lower()
    cb2 = SimpleNamespace(from_user=SimpleNamespace(id=5), data="hitl:999:approve", message=None, bot=None, answer=answer)
    await bot.on_hitl_callback(cb2)
    assert answers[-1] == "HITL не найден"


async def test_locator_reports_nothing_for_unknown_request(stores):
    await _project(stores, "d5-none")
    assert await hl.locate_hitl(12345) is None
