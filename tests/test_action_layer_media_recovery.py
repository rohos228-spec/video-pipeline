from __future__ import annotations

import pytest

from app.models import Project, ProjectStatus
from app.services import kill_switch, media_admission, media_recovery
from app.services.action_layer import executor
from app.services.action_layer import planner_rules as rules
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env

BASE = "/api/action-layer"
OPEN = {"node_type": "videos", "project_id": 1, "profile": None, "retry_same_max": 1,
        "submits_new_paid_request": False, "ledger": "ok",
        "keys": [{"key": "clip_005", "mode": "resume_poll", "code": "open_task", "task_id": "T-9",
                  "provider": "kie"}],
        "verdict": {"mode": "resume_poll", "code": "open_task", "reasons": []}}


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


@pytest.fixture
def provider(monkeypatch):
    state = {"report": dict(OPEN), "resume_calls": 0, "fingerprints": [frozenset({"T-9"})] * 2}

    async def assess(project, node_type):
        return {**state["report"], "node_type": node_type}

    async def resume_poll(project, node_type):
        state["resume_calls"] += 1
        return {**state["report"], "resumed": [{"key": "clip_005", "task_id": "T-9", "outcome": "downloaded",
                                                "error": None}], "stats": {"checked": 1, "downloaded": 1},
                "new_paid_requests": 0}

    async def fingerprint(project_id):
        return state["fingerprints"].pop(0) if state["fingerprints"] else frozenset({"T-9"})

    async def verify_files(project, keys):
        return [{"key": k, "status": "passed", "path": f"videos/{k}_ab12cd34.mp4"} for k in keys]

    monkeypatch.setattr(media_recovery, "assess", assess)
    monkeypatch.setattr(media_recovery, "resume_poll", resume_poll)
    monkeypatch.setattr(media_admission, "task_fingerprint", fingerprint)
    monkeypatch.setattr(media_admission, "verify_files", verify_files)
    return state


async def _preview(env, steps):
    return await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})


async def _run(env, steps):
    r = await _preview(env, steps)
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    return r.json()["preview"], (await env.client.get(f"{BASE}/plans/{plan}")).json()


async def _status(env, pid, status):
    async with env.factory() as s:
        p = await s.get(Project, pid)
        p.status = ProjectStatus(status)
        await s.commit()


def test_both_actions_are_registered_with_honest_tiers_and_guard_hints() -> None:
    read = registry.get("media.get_recovery_assessment")
    resume = registry.get("media.resume_poll")
    assert (read.risk, read.spends_money, read.destructive) == ("read", False, False)
    assert (resume.risk, resume.spends_money, resume.destructive) == ("local_safe", False, False)
    assert resume.idempotent and not resume.needs_approval and resume.estimator is None
    assert "новых платных запросов не отправляет" in resume.description_ru.lower()
    for spec in (read, resume):
        assert spec.title_ru.strip() and spec.description_ru.strip()
        assert rules.is_guarded(spec), spec.id
        assert spec.id in rules.GUARD_HINTS
    assert rules.unguarded_risky(registry) == []
    assert registry.get("media.resume_poll").params_schema["properties"]["node_type"]["enum"] == [
        "videos", "items", "music"]


def test_guard_hints_keep_the_media_steps_only_for_matching_requests() -> None:
    resume, read = registry.get("media.resume_poll"), registry.get("media.get_recovery_assessment")
    assert rules.request_hints_action("докачай видео кадра 5", resume)
    assert not rules.request_hints_action("проверь, что с задачей Kie", resume)
    assert rules.request_hints_action("проверь, что с задачей Kie", read)
    assert not rules.request_hints_action("смонтируй ролик", resume)
    steps = [{"action_id": "media.resume_poll", "params": {}, "depends_on": []}]
    kept, dropped = rules.drop_unrequested(steps, "проверь, что с задачей Kie", registry)
    assert kept == [] and dropped == ["Докачать медиа по известной задаче провайдера"]


async def test_assessment_action_reads_the_verdict_without_approval(env, provider) -> None:
    pid = await env.add_project(frames=1)
    preview, plan = await _run(env, [{"action_id": "media.get_recovery_assessment",
                                      "params": {"project_id": pid, "node_type": "videos"}}])
    assert preview["needs_approval"] is False and preview["risk_max"] == "read"
    result = plan["steps"][0]["result"]
    assert result["verdict"]["mode"] == "resume_poll"
    assert result["supervisor_admission"] == {"admitted_node": True, "operation": "resume_poll", "denied": None}
    assert result["submits_new_paid_request"] is False and provider["resume_calls"] == 0


async def test_resume_poll_runs_without_approval_and_reports_zero_new_requests(env, provider) -> None:
    pid = await env.add_project(frames=1)
    preview, plan = await _run(env, [{"action_id": "media.resume_poll",
                                      "params": {"project_id": pid, "node_type": "videos"}}])
    assert preview["needs_approval"] is False and preview["risk_max"] == "local_safe"
    assert preview["cost"]["high"] == 0.0
    result = plan["steps"][0]["result"]
    assert (result["verified"], result["operation"], result["new_paid_requests"]) == (True, "resume_poll", 0)
    assert result["resumed"][0]["outcome"] == "downloaded" and result["files"][0]["status"] == "passed"
    assert provider["resume_calls"] == 1


async def test_a_new_task_id_in_the_ledger_makes_the_result_unverified(env, provider) -> None:
    pid = await env.add_project(frames=1)
    provider["fingerprints"] = [frozenset({"T-9"}), frozenset({"T-9", "T-10"})]
    _, plan = await _run(env, [{"action_id": "media.resume_poll",
                                "params": {"project_id": pid, "node_type": "videos"}}])
    result = plan["steps"][0]["result"]
    assert result["verified"] is False and result["new_paid_requests"] == 1
    assert "new_provider_task_in_ledger" in result["problems"]


@pytest.mark.parametrize("report,reason", [
    ({"verdict": {"mode": "denied", "code": "unknown_outcome_kie", "reasons": []}, "keys": []},
     "исход прошлой платной отправки неизвестен"),
    ({"verdict": {"mode": "retry_same", "code": "provably_failed", "reasons": []}, "keys": []},
     "платную повторную отправку надзор не делает"),
    ({"verdict": {"mode": "nothing_to_do", "code": "downloaded", "reasons": []}, "keys": []},
     "возобновлять нечего"),
])
async def test_preview_is_refused_with_a_typed_reason_when_nothing_may_be_resumed(env, provider, report, reason) -> None:
    pid = await env.add_project(frames=1)
    provider["report"] = {**OPEN, **report}
    r = await _preview(env, [{"action_id": "media.resume_poll",
                              "params": {"project_id": pid, "node_type": "videos"}}])
    assert r.status_code == 422 and "resume_available" in str(r.json()) and reason in str(r.json())
    assert provider["resume_calls"] == 0


async def test_preview_is_refused_while_a_step_runs_and_for_unadmitted_nodes(env, provider) -> None:
    pid = await env.add_project(frames=1)
    await _status(env, pid, "generating_videos")
    r = await _preview(env, [{"action_id": "media.resume_poll",
                              "params": {"project_id": pid, "node_type": "videos"}}])
    assert r.status_code == 422 and "project_idle" in str(r.json())
    await _status(env, pid, "paused")
    for node in ("hero", "audio", "sfx_gen", "assemble", "images"):
        bad = await _preview(env, [{"action_id": "media.resume_poll",
                                    "params": {"project_id": pid, "node_type": node}}])
        assert bad.status_code == 422, node
    assert provider["resume_calls"] == 0


async def test_endpoint_refuses_under_the_kill_switch_and_for_a_busy_project(env, provider) -> None:
    pid = await env.add_project(frames=1)
    url = f"/api/projects/{pid}/media-recovery/videos/resume-poll"
    await _status(env, pid, "generating_videos")
    busy = await env.client.post(url)
    assert busy.status_code == 409 and busy.json()["detail"]["code"] == "project_busy"
    await _status(env, pid, "paused")
    await kill_switch.set_state(env.factory, active=True, actor="operator", rationale="emergency stop test")
    stopped = await env.client.post(url)
    assert stopped.status_code == 409 and stopped.json()["detail"]["code"] == "kill_switch_active"
    assert provider["resume_calls"] == 0
    await kill_switch.set_state(env.factory, active=False, actor="operator", rationale="emergency stop off")
    ok = await env.client.post(url)
    assert ok.status_code == 200 and ok.json()["verified"] is True and provider["resume_calls"] == 1
    other = await env.client.post(f"/api/projects/{pid}/media-recovery/hero/resume-poll")
    assert other.status_code == 409 and other.json()["detail"]["code"] == "node_not_admitted"
    unknown = await env.client.get(f"/api/projects/{pid}/media-recovery/publish")
    assert unknown.status_code == 404
