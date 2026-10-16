from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.models import ProjectStatus
from app.orchestrator.steps import publish as step
from app.services import operator_audit as oa
from app.services import publications_store as ps
from app.settings import settings
from app.web import deps
from app.web.routers import publications as router_mod
from tests.w6_1_support import PLATFORMS, Bot, Env, add_final, fake_registry, grant, make_project, sha_of

GOOD_URL = "https://example.invalid/post/1"


@pytest.fixture
async def env():
    e = await Env().create()
    yield e
    await e.close()


@pytest.fixture(autouse=True)
def _flags(monkeypatch):
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    monkeypatch.delenv("VP_OPERATOR_AUDIT", raising=False)
    oa.reset_stats()
    oa._ensured.clear()


@pytest.fixture
async def ctx(env, monkeypatch):
    app = FastAPI()
    app.add_middleware(oa.OperatorAuditMiddleware)
    app.include_router(router_mod.router, prefix="/api")

    async def _session():
        async with env.maker() as s:
            yield s

    app.dependency_overrides[deps.get_session] = _session
    clicks = fake_registry(monkeypatch, {pl: "unconfirmed" for pl in PLATFORMS})
    async with env.maker() as db:
        p = await make_project(db, env.workdir)
        await grant(db, p)
        await step.run(db, p, Bot())
        await db.commit()
        pid, sha = p.id, await sha_of(db, p)
        assert p.status is ProjectStatus.assembled
    client = AsyncClient(transport=ASGITransport(app=app, client=("127.0.0.1", 5000)), base_url="http://localhost")
    yield SimpleCtx(client, env, pid, sha, clicks)
    await client.aclose()


class SimpleCtx:
    def __init__(self, client, env, pid, sha, clicks):
        self.client, self.env, self.pid, self.sha, self.clicks = client, env, pid, sha, clicks

    def url(self, tail=""):
        return f"/api/projects/{self.pid}/publications{tail}"

    async def project(self):
        from app.models import Project
        async with self.env.maker() as s:
            return await s.get(Project, self.pid)

    async def rows(self):
        async with self.env.maker() as s:
            return {r["platform"]: r for r in await ps.list_for(s, self.pid, self.sha)}


def _audit(pid):
    return [r for r in oa.list_rows(limit=500, project_id=pid) if "/publications" in r["path"]]




async def test_list_shows_platforms_status_sha_and_error(ctx):
    r = await ctx.client.get(ctx.url())
    assert r.status_code == 200
    body = r.json()
    assert body["sha256"] == ctx.sha and body["requires_human_approval"] is True
    assert [x["platform"] for x in body["platforms"]] == PLATFORMS
    first = body["platforms"][0]
    assert first["state"] == "unconfirmed" and "ждёт проверки" in first["state_label"]
    assert first["post_url"] == "https://up" and first["can_confirm_manual"] is True
    assert body["derived"]["state"] == ps.PS_NEEDS_HUMAN
    assert _audit(ctx.pid) == []


async def test_list_unknown_project_is_404(ctx):
    assert (await ctx.client.get("/api/projects/999999/publications")).status_code == 404




@pytest.mark.parametrize("tail,body", [
    ("/tiktok/confirm-manual", {"post_url": GOOD_URL}),
    ("/tiktok/mark-failed", {"reason": "проверил, поста нет"}),
    ("/approve", {"platforms": ["tiktok"], "caption_sha256": "c" * 64}),
])
async def test_mutations_without_valid_operator_token_are_denied_and_audited(ctx, tail, body):
    before = await ctx.rows()
    r = await ctx.client.post(ctx.url(tail), json={"sha256": ctx.sha, **body}, headers={"X-VP-Operator": "wrong-token"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "operator_token_required"
    assert await ctx.rows() == before
    [row] = _audit(ctx.pid)
    assert (row["method"], row["status_code"], row["outcome"]) == ("POST", 403, oa.OUTCOME_DENIED)




async def test_confirm_manual_writes_audit_and_does_not_click(ctx):
    r = await ctx.client.post(ctx.url("/tiktok/confirm-manual"), json={"sha256": ctx.sha, "post_url": GOOD_URL})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True and body["clicked_again"] is False and body["state"] == "confirmed"
    assert len(ctx.clicks) == 5
    assert ctx.clicks.opened == [1]
    row = (await ctx.rows())["tiktok"]
    assert row["status"] == ps.S_CONFIRMED and row["post_url"] == GOOD_URL
    [a] = _audit(ctx.pid)
    assert a["method"] == "POST" and a["path"].endswith("/tiktok/confirm-manual")
    assert a["status_code"] == 200 and a["outcome"] == oa.OUTCOME_OK and a["actor"] == "operator"
    assert a["project_id"] == ctx.pid and a["route_template"].endswith("/{platform}/confirm-manual")
    async with ctx.env.maker() as s:
        [act] = await ps.list_manual_actions(s, ctx.pid)
    assert (act["action"], act["platform"], act["actor"], act["post_url"]) == ("confirm", "tiktok", "operator", GOOD_URL)


async def test_actor_name_from_header_reaches_the_manual_log(ctx):
    r = await ctx.client.post(ctx.url("/vk_clips/confirm-manual"), json={"sha256": ctx.sha, "reason": "виден в кабинете"},
                              headers={"X-Action-Layer-Operator": "Ivan Ivanov\t<b>"})
    assert r.status_code == 200
    async with ctx.env.maker() as s:
        [act] = await ps.list_manual_actions(s, ctx.pid)
    assert act["actor"].startswith("operator:") and "\n" not in act["actor"]


async def test_confirm_manual_requires_url_or_reason(ctx):
    r = await ctx.client.post(ctx.url("/tiktok/confirm-manual"), json={"sha256": ctx.sha})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "evidence_required"
    r = await ctx.client.post(ctx.url("/tiktok/confirm-manual"), json={"sha256": ctx.sha, "reason": "ок"})
    assert r.status_code == 409
    assert (await ctx.rows())["tiktok"]["status"] == ps.S_UNCONFIRMED
    assert [a["outcome"] for a in _audit(ctx.pid)] == [oa.OUTCOME_REJECTED, oa.OUTCOME_REJECTED]


async def test_stale_sha_after_rebuild_is_rejected(ctx):
    async with ctx.env.maker() as s:
        from app.models import Project
        p = await s.get(Project, ctx.pid)
        await add_final(s, p, ctx.env.workdir, data=b"REBUILT", uuid="u-new")
        await s.commit()
    r = await ctx.client.post(ctx.url("/tiktok/confirm-manual"), json={"sha256": ctx.sha, "post_url": GOOD_URL})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_artifact"
    assert (await ctx.rows())["tiktok"]["status"] == ps.S_UNCONFIRMED


async def test_unknown_platform_is_404(ctx):
    r = await ctx.client.post(ctx.url("/myspace/confirm-manual"), json={"sha256": ctx.sha, "post_url": GOOD_URL})
    assert r.status_code == 404


async def test_repeat_confirm_is_idempotent(ctx):
    body = {"sha256": ctx.sha, "post_url": GOOD_URL}
    a = await ctx.client.post(ctx.url("/tiktok/confirm-manual"), json=body)
    b = await ctx.client.post(ctx.url("/tiktok/confirm-manual"), json=body)
    assert a.json()["changed"] is True and b.json()["changed"] is False
    async with ctx.env.maker() as s:
        assert len(await ps.list_manual_actions(s, ctx.pid)) == 1


async def test_project_leaves_the_waiting_state_when_all_platforms_are_confirmed_by_hand(ctx):
    for i, pl in enumerate(PLATFORMS):
        r = await ctx.client.post(ctx.url(f"/{pl}/confirm-manual"), json={"sha256": ctx.sha, "post_url": f"{GOOD_URL}{i}"})
        assert r.status_code == 200
    last = r.json()
    assert last["project_state"] == ps.PS_PUBLISHED and last["project_status"] == "published"
    p = await ctx.project()
    assert p.status is ProjectStatus.published and "publish_needs_human" not in p.meta
    assert set(p.meta["published_platforms"]) == set(PLATFORMS)
    assert len(ctx.clicks) == 5 and len(_audit(ctx.pid)) == 5


async def test_partial_manual_confirm_keeps_waiting_with_visible_marker(ctx):
    r = await ctx.client.post(ctx.url("/tiktok/confirm-manual"), json={"sha256": ctx.sha, "post_url": GOOD_URL})
    assert r.json()["project_state"] == ps.PS_NEEDS_HUMAN
    p = await ctx.project()
    assert p.status is ProjectStatus.assembled and "tiktok" not in p.meta["publish_needs_human"]["platforms"]
    assert "tiktok" in p.meta["publish_needs_human"]["confirmed"]


async def test_mark_failed_needs_reason_and_records_it(ctx):
    r = await ctx.client.post(ctx.url("/tiktok/mark-failed"), json={"sha256": ctx.sha})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "reason_required"
    r = await ctx.client.post(ctx.url("/tiktok/mark-failed"), json={"sha256": ctx.sha, "reason": "кабинет пуст"})
    assert r.status_code == 200 and r.json()["clicked_again"] is False
    assert (await ctx.rows())["tiktok"]["error_code"] == ps.MANUAL_FAILED_CODE
    async with ctx.env.maker() as s:
        [act] = await ps.list_manual_actions(s, ctx.pid)
    assert act["action"] == "mark_failed" and act["reason"] == "кабинет пуст"
    assert len(ctx.clicks) == 5


async def test_all_closed_by_human_gives_resolved_state_not_published(ctx):
    for pl in PLATFORMS:
        r = await ctx.client.post(ctx.url(f"/{pl}/mark-failed"), json={"sha256": ctx.sha, "reason": "проверил, поста нет"})
    assert r.json()["project_state"] == ps.PS_CLOSED
    p = await ctx.project()
    assert p.status is ProjectStatus.assembled and p.status is not ProjectStatus.published
    assert set(p.meta["publish_closed_by_human"]["platforms"]) == set(PLATFORMS)




async def test_approve_rules_stale_caption_unknown_platform_and_needs_human_first(ctx):
    csha = step.caption_sha256("тема ролика")
    base = {"sha256": ctx.sha, "caption_sha256": csha}
    r = await ctx.client.post(ctx.url("/approve"), json={**base, "platforms": ["tiktok"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "needs_human_first"
    r = await ctx.client.post(ctx.url("/approve"), json={**base, "platforms": ["myspace"]})
    assert r.status_code == 404
    r = await ctx.client.post(ctx.url("/approve"), json={**base, "caption_sha256": "d" * 64, "platforms": ["tiktok"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_caption"
    r = await ctx.client.post(ctx.url("/approve"), json={**base, "sha256": "f" * 64, "platforms": ["tiktok"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_artifact"


async def test_approve_after_republish_decision_queues_the_step_and_clicks_only_that_platform(ctx, monkeypatch):
    r = await ctx.client.post(ctx.url("/tiktok/mark-failed"), json={"sha256": ctx.sha, "reason": "поста нет, повторить", "allow_republish": True})
    assert r.status_code == 200, r.text
    p = await ctx.project()
    assert "tiktok" not in p.meta["publish_needs_human"]["platforms"]
    async with ctx.env.maker() as s:
        facts = await step.publication_facts(s, p)
    assert {x["platform"]: x["can_approve"] for x in facts["platforms"]}["tiktok"] is True
    csha = step.caption_sha256("тема ролика")
    r = await ctx.client.post(ctx.url("/approve"), json={"sha256": ctx.sha, "caption_sha256": csha, "platforms": ["tiktok"]})
    assert r.status_code == 200 and r.json()["queued_for_publish_step"] is True
    p = await ctx.project()
    assert p.status is ProjectStatus.publishing
    before = len(ctx.clicks)
    async with ctx.env.maker() as s:
        from app.models import Project
        proj = await s.get(Project, ctx.pid)
        await step.run(s, proj, Bot())
        await s.commit()
    assert ctx.clicks[before:] == ["tiktok"]
    assert (await ctx.project()).status is ProjectStatus.assembled
    assert [a["outcome"] for a in _audit(ctx.pid)] == [oa.OUTCOME_OK, oa.OUTCOME_OK]


async def test_approve_when_publish_disabled_records_but_does_not_queue(ctx, monkeypatch):
    await ctx.client.post(ctx.url("/tiktok/mark-failed"), json={"sha256": ctx.sha, "reason": "поста нет, повторить", "allow_republish": True})
    monkeypatch.setattr(settings, "social_publish_enabled", False)
    csha = step.caption_sha256("тема ролика")
    r = await ctx.client.post(ctx.url("/approve"), json={"sha256": ctx.sha, "caption_sha256": csha, "platforms": ["tiktok"]})
    assert r.status_code == 200 and r.json()["queued_for_publish_step"] is False and r.json()["publish_enabled"] is False
    assert (await ctx.project()).status is ProjectStatus.assembled




async def _approve_tiktok(ctx):
    r = await ctx.client.post(ctx.url("/tiktok/mark-failed"), json={"sha256": ctx.sha, "reason": "поста нет, повторить", "allow_republish": True})
    assert r.status_code == 200, r.text
    csha = step.caption_sha256("тема ролика")
    r = await ctx.client.post(ctx.url("/approve"), json={"sha256": ctx.sha, "caption_sha256": csha, "platforms": ["tiktok"]})
    assert r.status_code == 200 and r.json()["queued_for_publish_step"] is True


async def test_revoke_before_step_runs_means_no_click(ctx):
    await _approve_tiktok(ctx)
    assert (await ctx.project()).status is ProjectStatus.publishing
    r = await ctx.client.post(ctx.url("/tiktok/revoke"), json={"sha256": ctx.sha})
    assert r.status_code == 200, r.text
    assert r.json()["platform"] == "tiktok"
    async with ctx.env.maker() as s:
        facts = await step.publication_facts(s, await ctx.project())
    assert {x["platform"]: x["approved"] for x in facts["platforms"]}["tiktok"] is False
    before = len(ctx.clicks)
    async with ctx.env.maker() as s:
        from app.models import Project
        proj = await s.get(Project, ctx.pid)
        await step.run(s, proj, Bot())
        await s.commit()
    assert ctx.clicks[before:] == []
    assert (await ctx.project()).status is not ProjectStatus.publishing
    r = await ctx.client.post(ctx.url("/tiktok/revoke"), json={"sha256": ctx.sha})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_active_approval"
    csha = step.caption_sha256("тема ролика")
    r = await ctx.client.post(ctx.url("/approve"), json={"sha256": ctx.sha, "caption_sha256": csha, "platforms": ["tiktok"]})
    assert r.status_code == 200
    assert any(a["path"].endswith("/tiktok/revoke") for a in _audit(ctx.pid))


async def test_revoke_refused_when_approval_already_spent(ctx):
    await _approve_tiktok(ctx)
    async with ctx.env.maker() as s:
        from app.models import Project
        proj = await s.get(Project, ctx.pid)
        await step.run(s, proj, Bot())
        await s.commit()
    assert "tiktok" in ctx.clicks
    r = await ctx.client.post(ctx.url("/tiktok/revoke"), json={"sha256": ctx.sha})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_active_approval"


async def test_revoke_requires_operator_token_and_known_platform(ctx):
    r = await ctx.client.post(ctx.url("/myspace/revoke"), json={"sha256": ctx.sha})
    assert r.status_code == 404
    r = await ctx.client.post(ctx.url("/tiktok/revoke"), json={"sha256": ctx.sha}, headers={"X-VP-Operator": "wrong-token"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "operator_token_required"
