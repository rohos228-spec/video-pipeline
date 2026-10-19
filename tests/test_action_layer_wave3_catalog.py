from __future__ import annotations

import hashlib

import pytest

from app.models import Project, ProjectStatus
from app.services.action_layer import executor
from app.services.action_layer.context import ActionCallError
from app.services.action_layer.spec import ActionRegistry, ensure_builtin_actions, registry
from tests.action_layer_support import env

BASE = "/api/action-layer"

NEW_IDS = {
    "montage.upload_voice": ("destructive", False), "montage.upload_music": ("destructive", False),
    "montage.upload_image": ("destructive", False), "montage.upload_video": ("destructive", False),
    "montage.upload_ref": ("local_safe", False), "node.upload_file": ("destructive", False),
    "node.upload_check_agent": ("destructive", False),
    "node.clear_check_agent": ("destructive", False),
    "project.pause": ("local_safe", False), "project.resume": ("destructive", True),
    "project.continue_pipeline": ("destructive", True),
    "project.set_auto_mode": ("local_safe", False),
    "project.reload_xlsx": ("destructive", False),
    "montage.set_send_to_main_pc": ("local_safe", False),
    "prompt.get_master": ("read", False), "prompt.save_master": ("destructive", False),
    "prompt.create_master": ("local_safe", False), "prompt.delete_master": ("destructive", False),
    "node.get_run_cost_basis": ("read", False), "node.run_step": ("destructive", True),
}


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


@pytest.fixture
def not_managed(monkeypatch):
    async def _no(pid):
        return False
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _no)


async def _preview(env, steps):
    return await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})


async def _run(env, steps, *, approve=True):
    r = await _preview(env, steps)
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    if approve:
        a = await env.client.post(f"{BASE}/plans/{plan}/approve")
        assert a.status_code == 200, a.text
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    return (await env.client.get(f"{BASE}/plans/{plan}")).json()


async def _status(env, pid, status, **meta):
    async with env.factory() as s:
        p = await s.get(Project, pid)
        p.status = ProjectStatus(status)
        if meta:
            p.meta = {**(p.meta or {}), **meta}
        await s.commit()



def test_new_actions_registered_with_expected_tier_and_money_flags() -> None:
    assert len(registry) == 102
    for aid, (tier, spends) in NEW_IDS.items():
        spec = registry.get(aid)
        assert (spec.risk, spec.spends_money) == (tier, spends), aid
        assert spec.destructive == (tier == "destructive"), aid
        if tier == "destructive":
            assert spec.reversible is False and spec.destructive_effects is not None, aid
    assert registry.get("node.run_step").estimator is not None


def test_prompt_actions_are_global_scope() -> None:
    for aid in ("prompt.get_master", "prompt.save_master", "prompt.create_master",
                "prompt.delete_master"):
        spec = registry.get(aid)
        assert spec.scope == "global" and "project_id" not in spec.params_schema["properties"]
    assert registry.get("montage.upload_voice").scope == "project"


def test_project_start_actions_have_explicit_unknown_cost() -> None:
    for aid in ("project.resume", "project.continue_pipeline"):
        est = registry.get(aid).estimate({"project_id": 1})
        assert est.known is False and "воркер" in est.basis, aid



async def test_resume_and_continue_cannot_be_approved_unknown_cost(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    await _status(env, pid, "paused", paused_from_status="generating_images")
    r = await _preview(env, [{"action_id": "project.resume", "params": {"project_id": pid}}])
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    assert pv["needs_approval"] and pv["cost"]["unknown_steps"] == 1 and pv["budget"]["fits"] is False
    assert pv["steps"][0]["spends_money"] and pv["steps"][0]["destructive"]
    a = await env.client.post(f"{BASE}/plans/{r.json()['plan_id']}/approve")
    assert a.status_code == 409 and a.json()["detail"]["code"] == "over_budget"
    await _status(env, pid, "frames_ready")
    c = await _preview(env, [{"action_id": "project.continue_pipeline",
                              "params": {"project_id": pid}}])
    assert c.status_code == 200, c.text
    a = await env.client.post(f"{BASE}/plans/{c.json()['plan_id']}/approve")
    assert a.status_code == 409 and a.json()["detail"]["code"] == "over_budget"


async def test_resume_continue_preconditions(env, not_managed, monkeypatch) -> None:
    pid = await env.add_project(frames=1)
    r = await _preview(env, [{"action_id": "project.resume", "params": {"project_id": pid}}])
    assert r.status_code == 422 and "project_paused" in str(r.json())
    await _status(env, pid, "splitting")
    for aid in ("project.continue_pipeline",):
        r = await _preview(env, [{"action_id": aid, "params": {"project_id": pid}}])
        assert r.status_code == 422 and "project_idle" in str(r.json())
    await _status(env, pid, "paused")
    from app.services import montage_board_apply_job as job_mod
    _orig_apply_job = job_mod.get_apply_job
    monkeypatch.setattr(job_mod, "get_apply_job", lambda p: {"status": "running"})
    r = await _preview(env, [{"action_id": "project.resume", "params": {"project_id": pid}}])
    assert r.status_code == 422 and "no_montage_jobs" in str(r.json())
    monkeypatch.setattr(job_mod, "get_apply_job", _orig_apply_job)

    async def _yes(p):
        return True
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _yes)
    for aid in ("project.resume", "project.continue_pipeline", "project.pause"):
        r = await _preview(env, [{"action_id": aid, "params": {"project_id": pid}}])
        assert r.status_code == 422 and "not_managed" in str(r.json()), aid


async def test_pause_runs_without_approval_and_is_idempotent(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    await _status(env, pid, "frames_ready")
    r = await _preview(env, [{"action_id": "project.pause", "params": {"project_id": pid}}])
    assert r.status_code == 200 and r.json()["preview"]["needs_approval"] is False
    view = await _run(env, [{"action_id": "project.pause", "params": {"project_id": pid}}],
                      approve=False)
    assert view["status"] == "succeeded", view
    assert view["steps"][0]["result"]["status"] == "paused"
    det = (await env.client.get(f"/api/projects/{pid}")).json()
    assert det["status"] == "paused" and det["meta"]["paused_from_status"] == "frames_ready"
    again = await _run(env, [{"action_id": "project.pause", "params": {"project_id": pid}}],
                       approve=False)
    assert again["status"] == "succeeded"


class FakeCtx:
    def __init__(self, routes=None):
        self.calls, self.routes = [], routes or {}

    async def call(self, method, path, *, params=None, json=None, files=None, timeout=0):
        self.calls.append((method, path, params, json))
        val = self.routes.get((method, path), {})
        if isinstance(val, Exception):
            raise val
        return val(params, json) if callable(val) else val


async def test_resume_continue_executors_and_poll() -> None:
    resume = registry.get("project.resume")
    ctx = FakeCtx({("POST", "/projects/1/resume"): {"status": "generating_images"}})
    assert await resume.executor(ctx, {"project_id": 1}) == {
        "status": "generating_images", "resumed": True}
    cont = registry.get("project.continue_pipeline")
    ctx = FakeCtx({("POST", "/projects/1/continue"): {
        "action": "advanced", "status": "generating_images", "advanced": True, "cleared": []}})
    out = await cont.executor(ctx, {"project_id": 1})
    assert out["action"] == "advanced" and out["advanced"] is True
    state = {"status": "generating_images"}
    pctx = FakeCtx({("GET", "/projects/1"): lambda p, j: {"status": state["status"]}})
    assert (await cont.poll(pctx, {"project_id": 1}, out)).done is False
    state["status"] = "images_ready"
    done = await cont.poll(pctx, {"project_id": 1}, out)
    assert done.done and done.ok and done.result["final_status"] == "images_ready"
    assert resume.stop is not None and cont.stop is not None


async def test_real_resume_endpoint_restores_status_without_starting_anything(env) -> None:
    from app.services.action_layer.context import ActionContext
    pid = await env.add_project(frames=1)
    await _status(env, pid, "paused", paused_from_status="frames_ready")
    ctx = ActionContext(app=env.app, operator="t")
    out = await registry.get("project.resume").executor(ctx, {"project_id": pid})
    assert out["status"] == "frames_ready"
    det = (await env.client.get(f"/api/projects/{pid}")).json()
    assert "paused_from_status" not in det["meta"]



async def test_reload_xlsx_needs_approval_and_fails_cleanly_without_file(env) -> None:
    pid = await env.add_project(frames=1)
    r = await _preview(env, [{"action_id": "project.reload_xlsx", "params": {"project_id": pid}}])
    pv = r.json()["preview"]
    assert pv["needs_approval"] and pv["destructive_effects"]
    assert "project.xlsx" in pv["destructive_effects"][0]["text"]
    view = await _run(env, [{"action_id": "project.reload_xlsx", "params": {"project_id": pid}}])
    assert view["status"] == "failed" and "project.xlsx не найден" in view["steps"][0]["error"]


async def test_reload_xlsx_overwrites_db_from_file(env) -> None:
    from app.storage.project_sheet import ProjectSheet
    pid = await env.add_project(frames=1)
    async with env.factory() as s:
        p = await s.get(Project, pid)
        sheet = ProjectSheet(file_path=p.data_dir / "project.xlsx")
        sheet.ensure_initialized(project_id=p.id, slug=p.slug)
    view = await _run(env, [{"action_id": "project.reload_xlsx", "params": {"project_id": pid}}])
    assert view["status"] == "succeeded", view
    assert view["steps"][0]["result"]["reloaded"] is True


async def test_send_to_main_pc_flag_and_fleet_guard(env, monkeypatch) -> None:
    from app.settings import settings
    pid = await env.add_project(frames=1)
    view = await _run(env, [{"action_id": "montage.set_send_to_main_pc",
                             "params": {"project_id": pid, "enabled": True}}], approve=False)
    assert view["status"] == "succeeded", view
    meta = (await env.client.get(f"/api/projects/{pid}")).json()["meta"]
    assert meta["node_step_params"]["assemble"]["send_to_main_pc"] is True
    await _run(env, [{"action_id": "montage.set_assemble_params",
                      "params": {"project_id": pid, "bgm_level": 30}}], approve=False)
    meta = (await env.client.get(f"/api/projects/{pid}")).json()["meta"]
    assert meta["node_step_params"]["assemble"] == {"send_to_main_pc": True, "bgm_level": 30}
    monkeypatch.setattr(settings, "fleet_enabled", False)
    r = await _preview(env, [{"action_id": "montage.set_send_to_main_pc",
                              "params": {"project_id": pid, "enabled": True}}])
    assert r.status_code == 422 and "fleet_enabled" in str(r.json())
    r = await _preview(env, [{"action_id": "montage.set_send_to_main_pc",
                              "params": {"project_id": pid, "enabled": False}}])
    assert r.status_code == 200



@pytest.fixture
def prompts_root(tmp_path, monkeypatch):
    from app.services import prompt_library as pl
    root = tmp_path / "prompts_root"
    root.mkdir()
    monkeypatch.setattr(pl, "PROMPTS_ROOT", root)
    (pl.step_dir("plan") / "default.md").write_text("старый текст", encoding="utf-8")
    (pl.step_dir("plan") / "variant_a.md").write_text("вариант а", encoding="utf-8")
    return root


async def test_save_master_overwrites_with_digest_and_keeps_history(env, prompts_root) -> None:
    steps = [{"action_id": "prompt.save_master", "params": {
        "step_code": "plan", "name": "variant_a", "content": "новый текст"}}]
    r = await _preview(env, steps)
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    assert pv["needs_approval"] and pv["risk_max"] == "destructive"
    assert "ГЛОБАЛЬНО" in pv["destructive_effects"][0]["text"]
    assert (prompts_root / "01_plan" / "variant_a.md").read_text(encoding="utf-8") == "вариант а"
    view = await _run(env, steps)
    assert view["status"] == "succeeded", view
    res = view["steps"][0]["result"]
    assert res["previous_sha256"] == hashlib.sha256("вариант а".encode()).hexdigest()
    assert res["new_sha256"] == hashlib.sha256("новый текст".encode()).hexdigest()
    assert res["scope"] == "global" and res["unchanged"] is False
    assert (prompts_root / "01_plan" / "variant_a.md").read_text(encoding="utf-8") == "новый текст"
    hist = prompts_root / "01_plan" / ".history" / "variant_a"
    assert hist.is_dir() and any("вариант а" in p.read_text(encoding="utf-8")
                                 for p in hist.glob("*.md"))
    r = await env.client.get(f"/api/prompt-files/plan/variant_a/content")
    assert r.json()["content"] == "новый текст"


async def test_prompt_actions_preconditions(env, prompts_root) -> None:
    async def refused(action, params, code):
        r = await _preview(env, [{"action_id": action, "params": params}])
        assert r.status_code == 422 and code in str(r.json()), (action, params, r.text)
    await refused("prompt.save_master", {"step_code": "plan", "name": "нет_такого",
                                         "content": "x"}, "prompt_exists")
    await refused("prompt.save_master", {"step_code": "no_step", "name": "default",
                                         "content": "x"}, "prompt_exists")
    await refused("prompt.save_master", {"step_code": "plan", "name": "variant_a",
                                         "content": "   "}, "content_nonblank")
    await refused("prompt.create_master", {"step_code": "plan", "name": "variant_a",
                                           "content": "x"}, "prompt_free")
    await refused("prompt.delete_master", {"step_code": "plan", "name": "default"},
                  "not_default")
    for bad in ("..", "a..b", "x/y", "a:b"):
        r = await _preview(env, [{"action_id": "prompt.save_master", "params": {
            "step_code": "plan", "name": bad, "content": "x"}}])
        assert r.status_code == 422, bad
    assert (prompts_root / "01_plan" / "default.md").read_text(encoding="utf-8") == "старый текст"


async def test_create_get_and_delete_master(env, prompts_root) -> None:
    created = await _run(env, [{"action_id": "prompt.create_master", "params": {
        "step_code": "plan", "name": "fresh", "content": "новый вариант"}}], approve=False)
    assert created["status"] == "succeeded", created
    assert (prompts_root / "01_plan" / "fresh.md").read_text(encoding="utf-8") == "новый вариант"
    got = await _run(env, [{"action_id": "prompt.get_master", "params": {
        "step_code": "plan", "name": "fresh"}}], approve=False)
    out = got["steps"][0]["result"]
    assert out["content"] == "новый вариант" and out["chars"] == len("новый вариант")
    gone = await _run(env, [{"action_id": "prompt.delete_master", "params": {
        "step_code": "plan", "name": "fresh"}}])
    assert gone["status"] == "succeeded", gone
    assert gone["steps"][0]["result"]["previous_sha256"] == hashlib.sha256(
        "новый вариант".encode()).hexdigest()
    assert not (prompts_root / "01_plan" / "fresh.md").exists()


async def test_group_prompt_digest_reads_group_template_not_shared_file() -> None:
    from app.services.action_layer import builtin_wave3 as w3
    ctx = FakeCtx({("GET", "/prompt-files/excel_gpt/scenes_to_frames_ru/content"):
                   {"content": "шаблон группы"},
                   ("PUT", "/prompt-files/excel_gpt/scenes_to_frames_ru"): {}})
    params = {"step_code": "excel_gpt", "name": "scenes_to_frames_ru", "content": "новый"}
    out = await w3._save_master(ctx, params)
    assert ctx.calls[0][:3] == ("GET", "/prompt-files/excel_gpt/scenes_to_frames_ru/content",
                                {"group_id": "script_frames_qc"})
    assert out["previous_sha256"] == hashlib.sha256("шаблон группы".encode()).hexdigest()
    effects = registry.get("prompt.save_master").destructive_effects(params)
    assert "шаблона группы script_frames_qc" in effects
    plain = {"step_code": "plan", "name": "default", "content": "x"}
    assert "script_frames_qc" not in registry.get("prompt.save_master").destructive_effects(plain)
    ctx2 = FakeCtx({("GET", "/prompt-files/plan/default/content"): {"content": "a"}})
    await w3._save_master(ctx2, plain)
    assert ctx2.calls[0][2] is None


async def test_missing_prompt_executor_errors_not_blind_write() -> None:
    from app.services.action_layer import builtin_wave3 as w3
    ctx = FakeCtx({("GET", "/prompt-files/plan/x/content"): ActionCallError(404, "no")})
    with pytest.raises(RuntimeError, match="не найден"):
        await w3._save_master(ctx, {"step_code": "plan", "name": "x", "content": "c"})
    assert not any(c[0] in ("PUT", "DELETE") for c in ctx.calls)


async def test_set_auto_mode_toggles_without_approval_and_refuses_enable_while_running(
        env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    await _status(env, pid, "frames_ready")
    step = lambda on: [{"action_id": "project.set_auto_mode",
                        "params": {"project_id": pid, "enabled": on}}]
    pre = await _preview(env, step(True))
    assert pre.status_code == 200 and pre.json()["preview"]["needs_approval"] is False
    view = await _run(env, step(True), approve=False)
    assert view["status"] == "succeeded", view["steps"][0].get("error")
    assert view["steps"][0]["result"] == {"auto_mode": True}
    async with env.factory() as s:
        row = await s.get(Project, pid)
        assert row.auto_mode is True and (row.meta or {}).get("auto_await_manual_start") is True
    assert (await _run(env, step(True), approve=False))["status"] == "succeeded"
    await _status(env, pid, "generating_images")
    r = await _preview(env, step(True))
    assert r.status_code == 422 and "auto_mode_enable_safe" in str(r.json())
    assert (await _preview(env, step(False))).status_code == 200
    await _status(env, pid, "frames_ready")
    off = await _run(env, step(False), approve=False)
    assert off["status"] == "succeeded"
    async with env.factory() as s:
        row = await s.get(Project, pid)
        assert row.auto_mode is False and not (row.meta or {}).get("auto_await_manual_start")


def test_every_action_and_macro_param_has_a_russian_label_in_the_ui() -> None:
    import re
    from pathlib import Path

    from app.services.action_layer import macros
    src = (Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "action-layer.ts").read_text(
        encoding="utf-8")
    labelled = set(re.findall(r"^\s{2}([A-Za-z_0-9]+): \{ label:", src, re.M))
    names = {k for s in registry.list() for k in s.params_schema["properties"]}
    names |= {k for m in macros.BUILTIN_MACROS for k in m.params_schema["properties"]}
    assert sorted(names - labelled - {"project_id"}) == []
