from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.bots.outsee_http import OutseeSubmitUnknownError
from app.models import Project, ProjectStatus
from app.services import kill_switch, media_resume, media_spend, media_unknown, outsee_guard, spend_guard
from app.services.action_layer import executor, store
from app.services.action_layer import planner_rules as rules
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env

BASE = "/api/action-layer"
REASON = "Сверил историю задач и баланс в кабинете: списания по этому ключу не было"


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


@pytest.fixture
def ledger(env, monkeypatch):
    monkeypatch.setattr("app.db.SessionLocal", env.factory)
    monkeypatch.setattr(media_spend, "_enabled", lambda provider: True)
    monkeypatch.setattr(media_spend, "_managed", lambda: False)
    return env


async def _intent(env, pid, key, provider="kie", intent_id="aaaa1111", status="submit_unknown", ago_s=0,
                  model="m-1", event="intent"):
    async with env.factory() as s:
        await spend_guard.ensure_task_schema(s)
        s.add(spend_guard.ProviderTaskLedger(
            project_id=pid, key=key, task_id=spend_guard.intent_task_id(intent_id), provider=provider,
            api="outsee" if provider == "outsee" else "jobs", model=model, event=event, status=status,
            note="ph=0123456789abcdef", created_at=datetime.utcnow() - timedelta(seconds=ago_s)))
        await s.commit()


async def _history(env):
    async with env.factory() as s:
        rows = (await s.scalars(select(store.ActionHistory).order_by(store.ActionHistory.id))).all()
    return [h for h in rows if h.event.startswith("media_release")]


async def _status(env, pid, status):
    async with env.factory() as s:
        p = await s.get(Project, pid)
        p.status = ProjectStatus(status)
        await s.commit()


async def _plan(env, steps):
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})
    assert r.status_code == 200, r.text
    return r.json()["plan_id"], r.json()["preview"]


async def _run(env, plan_id, *, approve=True):
    if approve:
        a = await env.client.post(f"{BASE}/plans/{plan_id}/approve")
        assert a.status_code == 200, a.text
    assert (await env.client.post(f"{BASE}/plans/{plan_id}/run")).status_code == 200
    await executor.wait(plan_id)
    return (await env.client.get(f"{BASE}/plans/{plan_id}")).json()


def _release_step(pid, key="clip_005", provider="kie", reason=REASON):
    return {"action_id": "media.release_unknown",
            "params": {"project_id": pid, "key": key, "provider": provider, "reason": reason}}



def test_both_actions_are_registered_with_honest_tiers_and_guard_hints() -> None:
    listing, release = registry.get("media.list_unknown_outcomes"), registry.get("media.release_unknown")
    assert (listing.risk, listing.spends_money, listing.destructive) == ("read", False, False)
    assert listing.idempotent and not listing.needs_approval
    assert (release.risk, release.destructive, release.reversible, release.idempotent) == (
        "destructive", True, False, False)
    assert release.needs_approval and release.destructive_effects is not None
    props = release.params_schema["properties"]
    assert set(release.params_schema["required"]) == {"project_id", "key", "provider", "reason"}
    assert props["provider"]["enum"] == ["kie", "outsee"] and props["reason"]["minLength"] >= 10
    for spec in (listing, release):
        assert spec.title_ru.strip() and spec.description_ru.strip() and rules.is_guarded(spec), spec.id
    assert rules.unguarded_risky(registry) == []
    assert "второе списание возможно" in release.description_ru.lower()
    text = release.destructive_effects({"project_id": 3, "key": "frame_012", "provider": "outsee", "reason": REASON})
    assert "ВТОРОЕ СПИСАНИЕ" in text and "Outsee" in text and "frame_012" in text and REASON[:20] in text


def test_guard_hints_keep_release_only_for_requests_that_name_a_block() -> None:
    release, listing = registry.get("media.release_unknown"), registry.get("media.list_unknown_outcomes")
    assert rules.request_hints_action("сними блокировку неизвестного исхода по клипу 5", release)
    assert rules.request_hints_action("разблокируй кадр 12 в Outsee", release)
    assert not rules.request_hints_action("докачай видео кадра 5", release)
    assert not rules.request_hints_action("смонтируй ролик", release)
    assert rules.request_hints_action("что заблокировано из-за неизвестного исхода", listing)
    steps = [{"action_id": "media.release_unknown", "params": {}, "depends_on": []}]
    kept, dropped = rules.drop_unrequested(steps, "докачай видео кадра 5", registry)
    assert kept == [] and dropped == ["Снять блокировку неизвестного исхода отправки"]



async def test_list_shows_provider_age_window_and_hides_resolved_and_expired(ledger) -> None:
    env = ledger
    pid = await env.add_project(frames=1)
    await _intent(env, pid, "clip_005", "kie", "k1k1k1k1", ago_s=120)
    await _intent(env, pid, "frame_012", "outsee", "o1o1o1o1", ago_s=30)
    await _intent(env, pid, "clip_006", "kie", "k2k2k2k2")
    await _intent(env, pid, "clip_006", "kie", "k2k2k2k2", status="bound", event="intent_done")
    await _intent(env, pid, "clip_007", "kie", "k3k3k3k3", ago_s=media_resume.window_s() + 600)
    await _intent(env, pid + 99, "clip_005", "kie", "k4k4k4k4")
    r = await env.client.get(f"/api/projects/{pid}/media-recovery/unknown-outcomes")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["window_s"] == media_resume.window_s() and body["window_enabled"] is True
    rows = {(row["key"], row["provider"]): row for row in body["blocked"]}
    assert set(rows) == {("clip_005", "kie"), ("frame_012", "outsee")} and body["count"] == 2
    kie = rows[("clip_005", "kie")]
    assert 100 <= kie["age_s"] <= 400 and kie["window_s"] == media_resume.window_s() and kie["may_be_billed"] is True
    assert kie["intent_ids"] == ["intent:k1k1k1k1"] and kie["blocked_until"] > kie["first_at"]
    assert (await env.client.get(f"/api/projects/{pid + 500}/media-recovery/unknown-outcomes")).status_code == 404
    assert (await env.client.get(f"/api/projects/{pid}/media-recovery/publish")).status_code == 404


async def test_list_action_runs_without_approval(ledger) -> None:
    env = ledger
    pid = await env.add_project(frames=1)
    await _intent(env, pid, "clip_005", "kie")
    plan_id, preview = await _plan(env, [{"action_id": "media.list_unknown_outcomes", "params": {"project_id": pid}}])
    assert preview["needs_approval"] is False and preview["risk_max"] == "read"
    plan = await _run(env, plan_id, approve=False)
    assert plan["status"] == "succeeded"
    assert plan["steps"][0]["result"]["blocked"][0]["key"] == "clip_005"


async def test_list_is_empty_when_the_resume_window_is_off(ledger, monkeypatch) -> None:
    env = ledger
    pid = await env.add_project(frames=1)
    await _intent(env, pid, "clip_005", "kie")
    monkeypatch.setattr(media_resume, "window_s", lambda: 0)
    body = (await env.client.get(f"/api/projects/{pid}/media-recovery/unknown-outcomes")).json()
    assert body["blocked"] == [] and body["window_enabled"] is False



async def test_release_needs_the_one_time_server_approval_and_shows_the_second_charge_warning(ledger) -> None:
    env = ledger
    pid = await env.add_project(frames=1)
    await _status(env, pid, "paused")
    await _intent(env, pid, "frame_012", "outsee", "o1o1o1o1")
    plan_id, preview = await _plan(env, [_release_step(pid, "frame_012", "outsee")])
    assert preview["needs_approval"] is True and preview["risk_max"] == "destructive"
    effect = preview["destructive_effects"][0]["text"]
    assert "ВТОРОЕ СПИСАНИЕ" in effect and "Outsee" in effect and "сверки" in effect
    refused = await env.client.post(f"{BASE}/plans/{plan_id}/run")
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "approval_required"
    assert [b["key"] for b in (await media_unknown.list_unknown(pid))["blocked"]] == ["frame_012"]
    assert await _history(env) == []
    plan = await _run(env, plan_id)
    assert plan["status"] == "succeeded", plan
    result = plan["steps"][0]["result"]
    assert (result["released"], result["provider"], result["key"]) == (1, "outsee", "frame_012")
    assert result["second_charge_possible"] is True and result["approved_by"] == "local_web_operator"
    assert (await media_unknown.list_unknown(pid))["blocked"] == []
    assert (await env.client.post(f"{BASE}/plans/{plan_id}/run")).status_code == 409
    assert (await env.client.post(f"{BASE}/plans/{plan_id}/approve")).status_code == 409


async def test_the_audit_rows_carry_reason_actor_plan_before_and_after(ledger) -> None:
    env = ledger
    pid = await env.add_project(frames=1)
    await _status(env, pid, "paused")
    await _intent(env, pid, "clip_005", "kie", "k1k1k1k1")
    plan_id, _ = await _plan(env, [_release_step(pid, "clip_005", "kie")])
    await _run(env, plan_id)
    rows = await _history(env)
    assert [r.event for r in rows] == ["media_release_requested", "media_release_unknown"]
    assert [r.outcome for r in rows] == ["requested", "released"]
    for row in rows:
        assert row.action_id == "media.release_unknown" and row.plan_id == plan_id
        assert row.actor == "local_web_operator"
        assert row.params["reason"] == REASON and row.params["key"] == "clip_005" and row.params["provider"] == "kie"
    assert rows[1].params["released"] == 1 and rows[1].params["second_charge_possible"] is True
    from sqlalchemy.exc import DatabaseError
    async with env.factory() as s:
        row = (await s.scalars(select(store.ActionHistory).where(store.ActionHistory.event == "media_release_unknown"))).one()
        row.outcome = "tampered"
        with pytest.raises(DatabaseError):
            await s.commit()


async def test_kie_release_resolves_only_kie_intents_and_the_outsee_block_of_the_same_key_stays(ledger) -> None:
    env = ledger
    pid = await env.add_project(frames=1)
    await _intent(env, pid, "clip_005", "kie", "k1k1k1k1")
    await _intent(env, pid, "clip_005", "outsee", "o1o1o1o1")
    out = await media_unknown.release(pid, "clip_005", "kie", reason=REASON, actor="op")
    assert out["released"] == 1 and out["provider"] == "kie"
    left = (await media_unknown.list_unknown(pid))["blocked"]
    assert [(r["key"], r["provider"]) for r in left] == [("clip_005", "outsee")]
    ctx = outsee_guard.Ctx(pid, "clip_005", "m", "video", "ph")
    with pytest.raises(OutseeSubmitUnknownError):
        await outsee_guard.begin(ctx)
    out = await media_unknown.release(pid, "clip_005", "outsee", reason=REASON, actor="op")
    assert out["released"] == 1
    await outsee_guard.begin(ctx)
    assert (await media_unknown.list_unknown(pid))["blocked"] == []


async def test_released_kie_key_is_no_longer_blocked_for_the_native_resume_guard(ledger) -> None:
    env = ledger
    pid = await env.add_project(frames=1)
    await _intent(env, pid, "clip_005", "kie", "k1k1k1k1")
    assert await media_resume.pending_for_key(pid, "clip_005") is True
    await media_unknown.release(pid, "clip_005", "kie", reason=REASON, actor="op")
    assert await media_resume.pending_for_key(pid, "clip_005") is False


@pytest.mark.parametrize("reason", ["", "ok", "   коротко  ", "а" * 9])
async def test_a_missing_or_short_reason_is_refused_everywhere(ledger, reason) -> None:
    env = ledger
    pid = await env.add_project(frames=1)
    await _status(env, pid, "paused")
    await _intent(env, pid, "clip_005", "kie")
    with pytest.raises(media_unknown.ReleaseRefused) as caught:
        await media_unknown.release(pid, "clip_005", "kie", reason=reason, actor="op")
    assert caught.value.code == "reason_required"
    http = await env.client.post(f"/api/projects/{pid}/media-recovery/release-unknown",
                                 json={"key": "clip_005", "provider": "kie", "reason": reason})
    assert http.status_code == 422
    bad = await env.client.post(f"{BASE}/plans/preview", json={"steps": [_release_step(pid, reason=reason)]})
    assert bad.status_code == 422
    assert len((await media_unknown.list_unknown(pid))["blocked"]) == 1 and await _history(env) == []


async def test_preview_is_refused_without_a_block_for_that_provider_or_while_a_step_runs(ledger) -> None:
    env = ledger
    pid = await env.add_project(frames=1)
    await _status(env, pid, "paused")
    await _intent(env, pid, "clip_005", "kie")
    wrong = await env.client.post(f"{BASE}/plans/preview", json={"steps": [_release_step(pid, "clip_005", "outsee")]})
    assert wrong.status_code == 422 and "blocked" in str(wrong.json()) and "Outsee" in str(wrong.json())
    none = await env.client.post(f"{BASE}/plans/preview", json={"steps": [_release_step(pid, "clip_099", "kie")]})
    assert none.status_code == 422 and "нет блокировки" in str(none.json())
    await _status(env, pid, "generating_videos")
    busy = await env.client.post(f"{BASE}/plans/preview", json={"steps": [_release_step(pid, "clip_005", "kie")]})
    assert busy.status_code == 422 and "project_idle" in str(busy.json())
    assert len((await media_unknown.list_unknown(pid))["blocked"]) == 1


async def test_endpoint_refuses_under_the_kill_switch_and_for_a_busy_project_and_audits_a_refusal(ledger) -> None:
    env = ledger
    pid = await env.add_project(frames=1)
    url = f"/api/projects/{pid}/media-recovery/release-unknown"
    body = {"key": "clip_005", "provider": "kie", "reason": REASON}
    await _intent(env, pid, "clip_005", "kie")
    await _status(env, pid, "generating_videos")
    busy = await env.client.post(url, json=body)
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "project_busy"
    await _status(env, pid, "paused")
    await kill_switch.set_state(env.factory, active=True, actor="operator", rationale="emergency stop test")
    stopped = await env.client.post(url, json=body)
    assert stopped.status_code == 409 and stopped.json()["detail"]["code"] == "kill_switch_active"
    assert len((await media_unknown.list_unknown(pid))["blocked"]) == 1
    await kill_switch.set_state(env.factory, active=False, actor="operator", rationale="emergency stop off")
    missing = await env.client.post(url, json={**body, "key": "clip_099"})
    assert missing.status_code == 409 and missing.json()["detail"]["code"] == "not_blocked"
    assert (await _history(env))[-1].outcome == "refused"
    ok = await env.client.post(url, json=body, headers={"X-Action-Layer-Operator": "web_operator:abc"})
    assert ok.status_code == 200 and ok.json()["released"] == 1 and ok.json()["approved_by"] == "web_operator:abc"
    again = await env.client.post(url, json=body)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "not_blocked"
    bad = await env.client.post(url, json={**body, "provider": "elevenlabs"})
    assert bad.status_code == 422
    assert (await env.client.post(f"/api/projects/{pid + 500}/media-recovery/release-unknown", json=body)).status_code == 404


async def test_the_approver_in_the_audit_is_the_operator_of_the_approval_not_a_parameter(ledger) -> None:
    env = ledger
    pid = await env.add_project(frames=1)
    await _status(env, pid, "paused")
    await _intent(env, pid, "clip_005", "kie")
    step = _release_step(pid)
    step["params"]["approved_by"] = "someone-else"
    assert (await env.client.post(f"{BASE}/plans/preview", json={"steps": [step]})).status_code == 422
    plan_id, _ = await _plan(env, [_release_step(pid)])
    approved = await env.client.post(f"{BASE}/plans/{plan_id}/approve",
                                     headers={"Authorization": "Bearer t0k3n"})
    assert approved.status_code == 200
    operator = approved.json()["operator"]
    assert (await env.client.post(f"{BASE}/plans/{plan_id}/run")).status_code == 200
    await executor.wait(plan_id)
    assert {r.actor for r in await _history(env)} == {operator}


async def test_release_never_sends_a_request_to_a_provider(ledger, monkeypatch) -> None:
    env = ledger
    pid = await env.add_project(frames=1)
    await _status(env, pid, "paused")
    await _intent(env, pid, "clip_005", "kie")
    import httpx
    sent: list = []

    async def no_network(self, request, **kw):
        sent.append(str(request.url))
        raise AssertionError("the release must not contact any provider")

    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    from app.bots import kie_http
    monkeypatch.setattr(kie_http, "kie_post_json", lambda *a, **k: (_ for _ in ()).throw(AssertionError("createTask")))
    out = await media_unknown.release(pid, "clip_005", "kie", reason=REASON, actor="op")
    assert out["released"] == 1 and sent == []
