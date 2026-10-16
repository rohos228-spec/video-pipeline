from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.services import spend_guard
from app.services.action_layer import approval, builtin_montage_more as bmm, executor, planner, service, store
from app.services.action_layer.plan import Plan, build_preview
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env, fresh_registry, make_spec
from tests.test_w1_5_action_fact_timeout import (
    PROJ_SCHEMA, _Job, _clean_executor_globals, _go, _job_spec, _plan)

OP = approval.OperatorIdentity("web_operator:w51")
ROOT = Path(__file__).resolve().parents[1]



async def test_c10_orphan_survives_restart_via_journal_and_is_released_by_journal(env, monkeypatch) -> None:
    from app.settings import settings
    monkeypatch.setattr(settings, "action_layer_step_timeout_s", 0.3)
    monkeypatch.setenv("ACTION_LAYER_STOP_GRACE_S", "0.3")
    pid = await env.add_project()
    job = _Job(finish_on_stop=False)
    reg = fresh_registry(_job_spec(job))
    plan_id = await _plan(env, reg, {"action_id": "t.job", "params": {"project_id": pid}})
    await _go(env, reg, plan_id, approve=True)
    assert executor.orphan_busy(pid) is not None

    for info in list(executor._ORPHANS.values()):
        info["task"].cancel()
    executor._ORPHANS.clear()
    executor._CLAIMS.clear()
    assert executor.orphan_busy(pid) is None

    plan2 = await _plan(env, reg, {"action_id": "t.job", "params": {"project_id": pid}})
    await service.approve_plan(env.app, plan2, OP, registry=reg)
    with pytest.raises(service.RunRefused) as e:
        await service.prepare_run(plan2, OP, registry=reg)
    assert e.value.code == "project_busy" and "журнале" in str(e.value)

    await executor._journal_orphan_released(plan_id, "t.job")
    executor._CLAIMS.clear()
    await service.prepare_run(plan2, OP, registry=reg)


async def test_c10_journal_orphan_expires_with_the_observation_window(env, monkeypatch) -> None:
    from app.settings import settings
    monkeypatch.setattr(settings, "action_layer_step_timeout_s", 0.3)
    monkeypatch.setenv("ACTION_LAYER_STOP_GRACE_S", "0.3")
    pid = await env.add_project()
    job = _Job(finish_on_stop=False)
    reg = fresh_registry(_job_spec(job))
    plan_id = await _plan(env, reg, {"action_id": "t.job", "params": {"project_id": pid}})
    await _go(env, reg, plan_id, approve=True)
    async with store.sessions()() as s:
        assert await executor.orphan_busy_db(s, pid, "другой") is not None
        assert await executor.orphan_busy_db(s, pid, plan_id) is None
        assert await executor.orphan_busy_db(s, pid + 99, "x") is None
    monkeypatch.setenv("ACTION_LAYER_ORPHAN_MAX_S", "0")
    async with store.sessions()() as s:
        assert await executor.orphan_busy_db(s, pid, "другой") is None



def _read_spec(action_id: str):
    async def run(ctx, params):
        return {}
    return make_spec(action_id, run, schema=PROJ_SCHEMA)


async def test_c16_preview_rejects_steps_of_different_projects() -> None:
    reg = fresh_registry(_read_spec("t.read"))
    ctx = SimpleNamespace(readonly_copy=lambda: SimpleNamespace())
    two = Plan.from_json([{"action_id": "t.read", "params": {"project_id": 1}},
                          {"action_id": "t.read", "params": {"project_id": 2}}], max_steps=5)
    pv = await build_preview(two, reg, ctx)
    assert pv["ok"] is False and any("разных проектов" in e for e in pv["errors"])
    one = Plan.from_json([{"action_id": "t.read", "params": {"project_id": 3}},
                          {"action_id": "t.read", "params": {"project_id": 3}}], max_steps=5)
    assert (await build_preview(one, reg, ctx))["ok"] is True


def test_c16_every_paid_action_names_exactly_one_project() -> None:
    ensure_builtin_actions()
    for spec in registry._actions.values():
        if spec.spends_money:
            assert "project_id" in spec.params_schema.get("required", []), spec.id



REVIEWED_NO_FUTURE_SPEND = frozenset({
    "change.propose", "group.insert", "group.save_from_selection", "group.update",
    "kill_switch.engage", "media.resume_poll", "montage.align_audio", "montage.cancel_queue_ops",
    "montage.insert_frame", "montage.link_ref", "montage.move_image", "montage.queue_ops",
    "montage.set_assemble_params", "montage.set_coverage", "montage.set_send_to_main_pc",
    "montage.set_video_trim", "montage.set_voiceover", "montage.set_voiceover_span",
    "montage.swap_shots", "montage.swap_slots", "montage.unlink_ref", "montage.upload_ref",
    "node.set_disabled", "node.set_prompt_variant", "project.pause", "project.stop",
    "prompt.create_master", "shot.add_cell", "shot.edit_cell_voiceover", "shot.edit_field",
    "storage.upload_file",
})
DECLARES_FUTURE_SPEND = frozenset({
    "project.set_auto_mode", "project.patch_settings", "project.create", "project.create_child",
    "node.set_config",
})


def test_c15_every_unapproved_mutating_action_is_classified() -> None:
    ensure_builtin_actions()
    unclassified = []
    for spec in registry._actions.values():
        if spec.risk != "local_safe" or spec.needs_approval:
            continue
        if spec.id in DECLARES_FUTURE_SPEND:
            assert spec.future_spend_note, f"{spec.id}: нет future_spend_note"
        elif spec.id not in REVIEWED_NO_FUTURE_SPEND:
            unclassified.append(spec.id)
    assert not unclassified, ("новое local_safe-действие не классифицировано (C-15): объявите "
                              f"future_spend_note или внесите в REVIEWED_NO_FUTURE_SPEND: {unclassified}")


async def test_c15_preview_shows_future_spend_effect() -> None:
    ensure_builtin_actions()
    ctx = SimpleNamespace(readonly_copy=lambda: SimpleNamespace())
    async def _noop(c, p):
        return {}

    reg = fresh_registry(make_spec("t.cfg", _noop, risk="local_safe", schema=PROJ_SCHEMA,
                                   future_spend_note="Меняет цену будущих запусков."))
    plan = Plan.from_json([{"action_id": "t.cfg", "params": {"project_id": 1}}], max_steps=3)
    pv = await build_preview(plan, reg, ctx)
    assert pv["ok"] and pv["needs_approval"] is False
    assert pv["future_spend_effects"] == [
        {"index": 0, "action_id": "t.cfg", "text": "Меняет цену будущих запусков."}]
    assert pv["steps"][0]["future_spend_note"]



NEEDS_HUMAN_APPROVAL = frozenset({
    "publish.send", "storage.clear_files", "change.apply", "change.rollback", "history.rollback",
    "prompt.save_master", "group.delete", "hero.replace_image", "montage.delete_frame",
    "montage.delete_image", "montage.delete_video", "node.reset_from_step", "project.reload_xlsx",
    "project.continue_pipeline", "project.resume", "node.run_step", "step.run_step",
    "hitl.approve", "hitl.regenerate", "massfactory.start", "montage.apply_queue",
})


def test_c19_dangerous_actions_need_human_approval_regardless_of_planner_text() -> None:
    ensure_builtin_actions()
    for action_id in NEEDS_HUMAN_APPROVAL:
        spec = registry.get(action_id)
        assert spec is not None and spec.needs_approval, action_id
    doc = inspect.getdoc(planner.approval_bypass_intent)
    assert "НЕ является барьером" in doc



async def _planner_reserved(env) -> float:
    async with env.factory() as s:
        await spend_guard.ensure_schema(s)
        return float(await s.scalar(select(func.coalesce(func.sum(spend_guard.SpendLedger.usd), 0.0))
                                    .where(spend_guard.SpendLedger.source == "planner")) or 0.0)


async def test_c20_not_billed_failure_closes_the_planner_reserve(env) -> None:
    import httpx
    await planner._spend_gate(None)
    assert await _planner_reserved(env) == pytest.approx(spend_guard.PLANNER_ESTIMATE_USD)
    closed = await planner._close_failed_reserve(None, httpx.ConnectError("нет соединения"))
    assert closed is True
    assert await _planner_reserved(env) == pytest.approx(0.0)
    assert spend_guard.take_pending() is None


async def test_c20_unknown_outcome_keeps_the_reserve_and_clears_pending(env) -> None:
    import httpx
    await planner._spend_gate(None)
    closed = await planner._close_failed_reserve(None, httpx.ReadTimeout("таймаут чтения"))
    assert closed is False
    assert await _planner_reserved(env) == pytest.approx(spend_guard.PLANNER_ESTIMATE_USD)
    assert spend_guard.take_pending() is None


def test_c20_classification_of_failures() -> None:
    from app.services.gpt_api import GptApiError
    import httpx
    assert planner.definitely_not_billed(httpx.ConnectTimeout("t"))
    assert planner.definitely_not_billed(GptApiError("x", context={"status_code": 401}))
    assert not planner.definitely_not_billed(GptApiError("x", context={"status_code": 502}))
    assert not planner.definitely_not_billed(GptApiError("x", context={"status_code": 429}))
    assert not planner.definitely_not_billed(httpx.ReadTimeout("t"))
    assert not planner.definitely_not_billed(RuntimeError("?"))



async def test_c18_watchdog_alarm_goes_through_the_notification_point(monkeypatch) -> None:
    from app.services import supervisor_heartbeat
    from app.services.watchdog import Watchdog
    sent: list[tuple] = []

    async def fake_send(text, kind, project_id=None):
        sent.append((text, kind, project_id))

    monkeypatch.setattr(supervisor_heartbeat, "_send", fake_send)
    wd = Watchdog(SimpleNamespace())
    await wd._notify([{"incident_id": "i1", "execution_id": "e1", "project_id": 7, "node_key": "images",
                       "stall": {"kind": "execution_idle"}}])
    assert len(sent) == 1 and sent[0][1] == "watchdog_stalled" and sent[0][2] == 7
    assert "#7" in sent[0][0] and "images" in sent[0][0]


async def test_c18_alarm_delivery_failure_does_not_break_the_scan(monkeypatch) -> None:
    from app.services import supervisor_heartbeat
    from app.services.watchdog import Watchdog

    async def boom(*a, **k):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(supervisor_heartbeat, "_send", boom)
    await Watchdog(SimpleNamespace())._notify([{"execution_id": "e", "project_id": 1, "stall": {}}])


async def test_c18_heartbeat_is_beaten_between_observer_stages(monkeypatch) -> None:
    from app.services import supervisor_heartbeat
    from app.services.observation import Observer
    beats: list[int] = []
    monkeypatch.setattr(supervisor_heartbeat, "beat", lambda **k: beats.append(1))
    fake = SimpleNamespace(_stages={}, _clock=lambda: 0.0)

    async def stage():
        return None

    await Observer._run_stage(fake, "x", stage)
    assert len(beats) == 2



def test_n11_suno_estimate_follows_the_measurement_not_the_price_list() -> None:
    from app.services import kie_catalog, media_spend
    spec = kie_catalog.get_model("suno-music")
    est = kie_catalog.estimate_credits(spec, {"prompt": "x"})
    assert est["credits"] == 122 and est["usd"] == pytest.approx(0.61)
    assert media_spend.price_kie(spec, {"model": "V5_5"})["usd"] == pytest.approx(0.61)


def test_f02_price_calibration_table_matches_code_and_labels_the_estimates() -> None:
    from app.services import price_calibration as pc
    values = pc.code_values()
    assert {c.name for c in pc.TABLE} == set(values), "каждая константа в таблице калибровки и в code_values"
    for c in pc.TABLE:
        assert values[c.name] == c.value, f"{c.name}: значение в коде изменилось, обновите статус в таблице"
        assert c.status in {"measured", "single_probe", "estimate", "policy"}
        if c.status == "measured":
            assert c.probes >= 3, c.name
    assert pc.by_name()["SUNO_MUSIC_CREDITS"].status == "single_probe"
    assert pc.label_ru("PRICE_MARKUP") == "оценка" and pc.label_ru("нет такой") == "оценка"



def test_f09_outsee_apply_queue_is_fail_closed_unless_owner_sets_a_submit_price(monkeypatch) -> None:
    from app.settings import settings
    ensure_builtin_actions()
    params = {"project_id": 1, "max_image_ops": 2, "max_video_ops": 0,
              "image_generator": "gpt_image_2_vip", "image_resolution": "2k"}
    spec = registry.get("montage.apply_queue")
    monkeypatch.setattr(settings, "action_layer_outsee_submit_credits", 0.0)
    assert not spec.estimate(params).known
    monkeypatch.setattr(settings, "action_layer_outsee_submit_credits", 60.0)
    est = spec.estimate(params)
    assert est.known and est.high >= 2 * bmm.IMAGE_BILLED_ATTEMPTS * 60
    imp = registry.get("montage.improve_scene").estimate({
        "project_id": 1, "frame_id": 1, "prompt": "x", "max_image_ops": 2,
        "image_generator": "gpt_image_2_vip", "image_resolution": "2k"})
    assert imp.known
    assert spend_guard.MEDIA_SUBMIT_ESTIMATE_USD / 0.005 == pytest.approx(60.0)



def test_d6_publication_is_never_auto_approved() -> None:
    from app.models import HITLKind
    from app.orchestrator import auto_advance as aa
    kinds = {t.kind for t in aa.TRANSITIONS.values()}
    assert HITLKind.approve_publish not in kinds
    src = (ROOT / "app/orchestrator/auto_advance.py").read_text(encoding="utf-8")
    assert "approve_publish" not in src.replace("auto_mode и HITL_AUTO_APPROVE подтверждение не создают", "")
    ensure_builtin_actions()
    assert registry.get("publish.send").needs_approval


def test_d6_auto_approve_of_final_is_guarded_by_final_verified() -> None:
    src = (ROOT / "app/orchestrator/auto_advance.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_apply_approve")
    body = ast.get_source_segment(src, fn)
    guard = body.index("_final_verified")
    approve = body.index("HITLDecision.approved")
    assert "HITLKind.approve_final" in body[:guard] and guard < approve, \
        "проверка финала должна стоять до присвоения решения approved"
    assert "verification not passed" in body



def test_d8_get_project_and_get_run_do_not_recompute_status() -> None:
    src = (ROOT / "app/web/routers/projects.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for name in ("get_project", "get_project_run"):
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name)
        text = ast.get_source_segment(src, fn)
        assert "recompute_status" not in text, f"{name}: пересчёт статуса на GET был причиной N-5"
    get_fn = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "get_project")
    assert "rollback" in ast.get_source_segment(src, get_fn)
