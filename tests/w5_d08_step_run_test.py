"""W5-4 D08: запуск шагов конвейера как действие слоя с оценкой и потолком.

Эндпоинт запуска шага подменён заменителем (платных вызовов и воркера нет); чтение идёт по
настоящим эндпоинтам на временной БД.
"""
from __future__ import annotations

import re

import pytest

from app.models import Project, ProjectStatus
from app.services.action_layer import executor
from app.services.action_layer import node_run_cost as nrc
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env  # noqa: F401

BASE = "/api/action-layer"


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


@pytest.fixture
def not_managed(monkeypatch):
    async def _no(pid):
        return False
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _no)


@pytest.fixture
def steps_open(monkeypatch):
    """Статус проекта пересчитывается по данным (пустой проект откатывается назад): условие
    «шаг доступен» отдельно проверяет test_step_preconditions, остальным оно не мешает."""
    monkeypatch.setattr("app.telegram.menu.is_step_runnable", lambda step, status: True)


def _auto(stub, on: bool):
    """Флаг автопродвижения живёт в project.db, которого у проекта этого стенда нет:
    чтение проекта подменяется (запись и остальные поля настоящие)."""
    stub["auto_mode"] = on


@pytest.fixture
def stub_writes(monkeypatch):
    """Заменитель записывающих эндпоинтов: вызов записывается, ответ задан, настоящий не идёт."""
    state = {"patterns": {}, "calls": [], "auto_mode": None}
    original = ActionContext.call

    async def call(self, method, path, **kw):
        if method.upper() == "GET" and re.fullmatch(r"/projects/\d+", path)                 and state["auto_mode"] is not None:
            got = await original(self, method, path, **kw)
            return {**got, "auto_mode": state["auto_mode"]}
        if method.upper() != "GET":
            for pattern, answer in state["patterns"].items():
                if re.search(pattern, path):
                    state["calls"].append((method.upper(), path, kw.get("params"), kw.get("json")))
                    return answer
        return await original(self, method, path, **kw)

    monkeypatch.setattr(ActionContext, "call", call)
    return state


async def _preview(env, steps):
    return await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})


async def _set(env, pid, **fields):
    async with env.factory() as s:
        p = await s.get(Project, pid)
        for k, v in fields.items():
            setattr(p, k, ProjectStatus(v) if k == "status" else v)
        await s.commit()


def _kie_pair(kind: str):
    """Модель и разрешение, для которых в репозитории есть тариф Kie (ищется в коде, не задаётся)."""
    from app.generation_options import (
        IMAGE_GENERATORS_BY_ID, IMAGE_RESOLUTIONS_BY_ID, VIDEO_GENERATORS_BY_ID,
        VIDEO_RESOLUTIONS_BY_ID)
    from app.services.action_layer.builtin_montage_more import _kie_unit

    gens = IMAGE_GENERATORS_BY_ID if kind == "image" else VIDEO_GENERATORS_BY_ID
    ress = IMAGE_RESOLUTIONS_BY_ID if kind == "image" else VIDEO_RESOLUTIONS_BY_ID
    for g in gens:
        for r in ress:
            unit, _why = _kie_unit(kind, g, r)
            if unit is not None:
                return g, r, unit
    return None


# --- чистая оценка (node_run_cost) ------------------------------------------------------------

def test_media_units_counts_second_shot() -> None:
    frames = [{"has_shot2": False}, {"has_shot2": True}, {}]
    assert nrc.media_units(frames) == {"frames": 3, "images": 4, "videos": 4}
    assert nrc.media_units([]) == {"frames": 0, "images": 0, "videos": 0}


def test_assemble_is_free_and_publish_is_not_a_step() -> None:
    est = nrc.media_code_estimate("assemble", units=None)
    assert est.known and est.high == 0.0 and "локально" in est.basis
    assert "publish" not in nrc.MEDIA_STEPS
    assert nrc.media_code_estimate("publish", units=1).known is False


def test_unpriced_steps_are_unknown_until_the_operator_names_a_ceiling() -> None:
    for code in ("audio", "music", "img_pr", "anim_pr"):
        est = nrc.media_code_estimate(code, units=None)
        assert est.known is False, code
        capped = nrc.apply_ceiling(est, 12.5)
        assert (capped.low, capped.high) == (0.0, 12.5) and "потолок оператора" in capped.basis
        assert nrc.apply_ceiling(est, None).known is False


def test_image_estimate_uses_the_catalog_tariff_and_ceiling_may_not_undercut_it() -> None:
    pair = _kie_pair("image")
    assert pair is not None, "в репозитории нет ни одной картиночной модели с тарифом Kie"
    gen, res, unit = pair
    est = nrc.media_code_estimate("img", units=4, image_generator=gen, image_resolution=res)
    assert est.known and est.high == round(4 * nrc.IMAGE_STEP_ATTEMPTS * unit, 2)
    assert "не сверялся с балансом" in est.basis
    assert nrc.apply_ceiling(est, est.high) is est
    assert nrc.apply_ceiling(est, est.high - 0.5).known is False
    assert nrc.media_code_estimate("img", units=None, image_generator=gen,
                                   image_resolution=res).known is False
    assert nrc.media_code_estimate("img", units=2, image_generator="no_such",
                                   image_resolution="1k").known is False


def test_video_estimate_names_the_clip_length_assumption() -> None:
    pair = _kie_pair("video")
    if pair is None:
        pytest.skip("нет видеомодели с тарифом Kie в репозитории")
    gen, res, unit = pair
    est = nrc.media_code_estimate("video", units=3, video_generator=gen, video_resolution=res)
    assert est.known and est.high == round(3 * nrc.VIDEO_STEP_ATTEMPTS * unit, 2)
    assert "допущен" in est.basis


# --- каталог ----------------------------------------------------------------------------------------

def test_step_actions_registered_with_tier_money_and_russian_text() -> None:
    spec = registry.get("step.run_step")
    assert (spec.risk, spec.spends_money, spec.destructive) == ("destructive", True, True)
    assert spec.estimator is not None and spec.poll is not None and spec.stop is not None
    assert set(spec.params_schema["properties"]["step_code"]["enum"]) == set(nrc.MEDIA_STEPS)
    assert "publish" not in spec.params_schema["properties"]["step_code"]["enum"]
    read = registry.get("step.get_run_cost_basis")
    assert read.risk == "read" and not read.spends_money
    assert registry.get("project.resume").params_schema["properties"]["ceiling_credits"]
    assert registry.get("project.continue_pipeline").params_schema["properties"]["ceiling_credits"]


def test_resume_and_continue_without_ceiling_keep_the_unknown_cost() -> None:
    for aid in ("project.resume", "project.continue_pipeline"):
        est = registry.get(aid).estimate({"project_id": 1})
        assert est.known is False and "воркер" in est.basis, aid
        capped = registry.get(aid).estimate({"project_id": 1, "ceiling_credits": 7})
        assert (capped.low, capped.high) == (0.0, 7.0), aid


# --- превью / подтверждение / запуск ------------------------------------------------------------------

async def test_cost_basis_reads_declared_values_from_the_project(env, not_managed) -> None:
    pid = await env.add_project(frames=3)
    r = await _preview(env, [{"action_id": "step.get_run_cost_basis",
                              "params": {"project_id": pid, "step_code": "img"}}])
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    got = (await env.client.get(f"{BASE}/plans/{plan}")).json()["steps"][0]["result"]
    assert got["kind"] == "image" and got["units"]["frames"] == 3 and got["units"]["images"] == 3
    assert got["declare"]["count"] == 3 and "image_generator" in got["declare"]
    assert got["uncalibrated"] is True


async def test_unpriced_step_needs_a_ceiling_then_preview_approve_run(env, not_managed,
                                                                      stub_writes, steps_open) -> None:
    pid = await env.add_project(frames=2)
    await _set(env, pid, status="audio_ready")
    stub_writes["patterns"][r"/steps/music/run$"] = {"id": pid}
    no_ceiling = await _preview(env, [{"action_id": "step.run_step",
                                       "params": {"project_id": pid, "step_code": "music"}}])
    assert no_ceiling.status_code == 422 and "step_cost_declared" in str(no_ceiling.json())

    ok = await _preview(env, [{"action_id": "step.run_step", "params": {
        "project_id": pid, "step_code": "music", "ceiling_credits": 40}}])
    assert ok.status_code == 200, ok.text
    pv = ok.json()["preview"]
    assert pv["needs_approval"] and pv["steps"][0]["spends_money"] and pv["steps"][0]["destructive"]
    assert pv["cost"]["high"] == 40 and pv["cost"]["unknown_steps"] == 0
    plan = ok.json()["plan_id"]

    # без подтверждения платный шаг не запускается
    denied = await env.client.post(f"{BASE}/plans/{plan}/run")
    assert denied.status_code in (403, 409) and not stub_writes["calls"]

    assert (await env.client.post(f"{BASE}/plans/{plan}/approve")).status_code == 200
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    done = (await env.client.get(f"{BASE}/plans/{plan}")).json()
    assert done["status"] == "succeeded", done
    assert stub_writes["calls"] == [("POST", f"/projects/{pid}/steps/music/run", {"mode": "resume"},
                                     None)]


async def test_approval_is_bound_to_the_ceiling(env, not_managed, stub_writes, steps_open) -> None:
    pid = await env.add_project(frames=1)
    await _set(env, pid, status="audio_ready")
    stub_writes["patterns"][r"/steps/music/run$"] = {"id": pid}

    async def make(ceiling):
        r = await _preview(env, [{"action_id": "step.run_step", "params": {
            "project_id": pid, "step_code": "music", "ceiling_credits": ceiling}}])
        assert r.status_code == 200, r.text
        return r.json()["plan_id"]

    cheap, dear = await make(10), await make(900)
    assert cheap != dear
    assert (await env.client.post(f"{BASE}/plans/{cheap}/approve")).status_code == 200
    # подтверждение дешёвого плана не открывает дорогой
    assert (await env.client.post(f"{BASE}/plans/{dear}/run")).status_code in (403, 409)
    assert stub_writes["calls"] == []


async def test_priced_image_step_is_approvable_without_a_ceiling(env, not_managed,
                                                                 stub_writes, steps_open) -> None:
    pair = _kie_pair("image")
    assert pair is not None
    gen, res, unit = pair
    from app.generation_options import clamp_image_resolution_id
    from app.services.action_layer.builtin_montage_more import _kie_unit
    res = clamp_image_resolution_id(gen, res)  # нода/проект приводят разрешение под модель
    unit = _kie_unit("image", gen, res)[0]
    pid = await env.add_project(frames=2)
    async with env.factory() as s:
        p = await s.get(Project, pid)
        p.status = ProjectStatus.image_prompts_ready
        p.image_generator, p.image_resolution = gen, res
        # нода «Картинки» перекрывает настройки проекта (как в test_action_layer_montage_more_real)
        p.meta = {"canvas_graph": {"nodes": [{"id": "n1", "type": "images",
                                              "data": {"modelId": gen.replace("_", "-")}}],
                                   "edges": []}}
        await s.commit()
    stub_writes["patterns"][r"/steps/img/run$"] = {"id": pid}
    params = {"project_id": pid, "step_code": "img", "count": 2, "image_generator": gen,
              "image_resolution": res}
    r = await _preview(env, [{"action_id": "step.run_step", "params": params}])
    assert r.status_code == 200, r.text
    cost = r.json()["preview"]["cost"]
    assert cost["high"] == round(2 * nrc.IMAGE_STEP_ATTEMPTS * unit, 2)
    # потолок ниже расчёта отклоняется условием
    low = await _preview(env, [{"action_id": "step.run_step",
                                "params": {**params, "ceiling_credits": cost["high"] / 2}}])
    assert low.status_code == 422 and "step_cost_declared" in str(low.json())
    # заявлено меньше генераций, чем на доске
    less = await _preview(env, [{"action_id": "step.run_step", "params": {**params, "count": 1}}])
    assert less.status_code == 422 and "step_cost_declared" in str(less.json())
    # заявлена другая модель, чем в проекте
    other = await _preview(env, [{"action_id": "step.run_step",
                                  "params": {**params, "image_resolution": "4k" if res != "4k" else "1k"}}])
    assert other.status_code == 422
    plan = r.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/approve")).status_code == 200
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    assert (await env.client.get(f"{BASE}/plans/{plan}")).json()["status"] == "succeeded"
    assert len(stub_writes["calls"]) == 1


async def test_assemble_is_free_and_needs_no_ceiling(env, not_managed, stub_writes, steps_open) -> None:
    pid = await env.add_project(frames=1)
    await _set(env, pid, status="audio_ready")
    stub_writes["patterns"][r"/steps/assemble/run$"] = {"id": pid}
    r = await _preview(env, [{"action_id": "step.run_step",
                              "params": {"project_id": pid, "step_code": "assemble"}}])
    assert r.status_code == 200, r.text
    assert r.json()["preview"]["cost"]["high"] == 0.0


async def test_step_preconditions(env, not_managed, stub_writes, monkeypatch) -> None:
    pid = await env.add_project(frames=1)
    params = {"project_id": pid, "step_code": "assemble"}
    step = [{"action_id": "step.run_step", "params": params}]
    # проект новый: сборка недоступна
    r = await _preview(env, step)
    assert r.status_code == 422 and "step_runnable" in str(r.json())
    monkeypatch.setattr("app.telegram.menu.is_step_runnable", lambda step, status: True)
    _auto(stub_writes, True)
    r = await _preview(env, step)
    assert r.status_code == 422 and "no_auto_chain" in str(r.json())
    _auto(stub_writes, False)
    await _set(env, pid, status="generating_images")
    r = await _preview(env, step)
    assert r.status_code == 422 and "project_idle" in str(r.json())
    await _set(env, pid, status="audio_ready")

    async def _yes(p):
        return True
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _yes)
    r = await _preview(env, step)
    assert r.status_code == 422 and "not_managed" in str(r.json())
    bad = await _preview(env, [{"action_id": "step.run_step",
                                "params": {"project_id": pid, "step_code": "publish"}}])
    assert bad.status_code in (400, 422) and not stub_writes["calls"]


async def test_resume_with_ceiling_is_approvable_and_without_it_is_not(env, not_managed,
                                                                       stub_writes) -> None:
    pid = await env.add_project(frames=1)
    await _set(env, pid, status="paused", meta={"paused_from_status": "generating_images"})
    stub_writes["patterns"][r"/resume$"] = {"status": "generating_images"}
    bare = await _preview(env, [{"action_id": "project.resume", "params": {"project_id": pid}}])
    assert bare.status_code == 200
    assert (await env.client.post(f"{BASE}/plans/{bare.json()['plan_id']}/approve")
            ).status_code == 409
    capped = await _preview(env, [{"action_id": "project.resume",
                                   "params": {"project_id": pid, "ceiling_credits": 25}}])
    assert capped.status_code == 200, capped.text
    assert capped.json()["preview"]["cost"]["high"] == 25
    plan = capped.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/approve")).status_code == 200
    assert stub_writes["calls"] == []  # подтверждение ничего не запускает
    _auto(stub_writes, True)
    chained = await _preview(env, [{"action_id": "project.resume",
                                    "params": {"project_id": pid, "ceiling_credits": 25}}])
    assert chained.status_code == 422 and "ceiling_chain" in str(chained.json())
