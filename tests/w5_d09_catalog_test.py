"""W5-4 D09: недостающие контролы Студии как действия слоя + законы каталога.

Эндпоинты настоящие, на временной БД; запуск воркера и платных вызовов нет (где нужен запуск,
записывающий эндпоинт подменён заменителем).
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import (
    Frame, FrameStatus, HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus)
from app.services.action_layer import builtin_wave5 as w5
from app.services.action_layer import executor
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env  # noqa: F401

BASE = "/api/action-layer"
DOC = Path(__file__).resolve().parents[1] / "docs" / "ACTION_LAYER_WAVE5.md"
CYR = re.compile(r"[А-Яа-яЁё]")

EXPECTED_TIER = {
    "step.get_run_cost_basis": ("read", False), "step.run_step": ("destructive", True),
    "hitl.list_pending": ("read", False), "hitl.approve": ("destructive", False),
    "hitl.reject": ("destructive", False), "hitl.regenerate": ("destructive", True),
    "project.create": ("local_safe", False), "project.create_child": ("local_safe", False),
    "project.patch_settings": ("local_safe", False), "massfactory.start": ("destructive", True),
    "kill_switch.engage": ("local_safe", False), "project.upload_xlsx": ("destructive", False),
    "hero.replace_image": ("destructive", False), "storage.upload_file": ("local_safe", False),
    "storage.clear_files": ("destructive", False), "history.list_snapshots": ("read", False),
    "history.rollback": ("destructive", False),
}


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


@pytest.fixture
def not_managed(monkeypatch):
    async def _no(pid):
        return False
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _no)


@pytest.fixture
def stub_writes(monkeypatch):
    state = {"patterns": {}, "calls": []}
    original = ActionContext.call

    async def call(self, method, path, **kw):
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


async def _run(env, steps, *, approve=False):
    r = await _preview(env, steps)
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    if approve:
        a = await env.client.post(f"{BASE}/plans/{plan}/approve")
        assert a.status_code == 200, a.text
    run = await env.client.post(f"{BASE}/plans/{plan}/run")
    assert run.status_code == 200, run.text
    await executor.wait(plan)
    return (await env.client.get(f"{BASE}/plans/{plan}")).json()


def step(action, **params):
    return {"action_id": action, "params": params}


# --- законы каталога --------------------------------------------------------------------------------

def test_every_new_action_has_russian_text_and_the_expected_tier() -> None:
    assert set(EXPECTED_TIER) == set(w5.WAVE5_IDS)
    for aid, (tier, spends) in EXPECTED_TIER.items():
        spec = registry.get(aid)
        assert (spec.risk, spec.spends_money) == (tier, spends), aid
        assert spec.destructive == (tier == "destructive"), aid
        assert CYR.search(spec.title_ru) and CYR.search(spec.description_ru), aid
        if spec.destructive:
            assert spec.destructive_effects is not None and CYR.search(
                spec.destructive_effects({k: _sample(v) for k, v in
                                          spec.params_schema["properties"].items()})), aid
        if spec.spends_money:
            assert spec.estimator is not None, aid


def _sample(schema: dict):
    t = schema.get("type")
    if "enum" in schema:
        return schema["enum"][0]
    return {"integer": 1, "number": 1.0, "boolean": False, "array": ["тема"],
            "string": "x"}.get(t, "x")


def test_every_action_in_the_catalog_has_russian_text_and_a_known_tier() -> None:
    for spec in registry.list():
        assert spec.risk in ("read", "local_safe", "paid", "destructive"), spec.id
        assert CYR.search(spec.title_ru) and CYR.search(spec.description_ru), spec.id


def test_catalog_counts_match_the_docs() -> None:
    text = DOC.read_text(encoding="utf-8")
    total = int(re.search(r"Всего действий: (\d+)", text).group(1))
    tiers = dict(re.findall(r"(read|local_safe|destructive|paid) (\d+)",
                            re.search(r"Распределение по уровням: ([^\n]+)", text).group(1)))
    actual = {}
    for spec in registry.list():
        actual[spec.risk] = actual.get(spec.risk, 0) + 1
    assert total == len(registry)
    assert {k: int(v) for k, v in tiers.items()} == actual


def test_docs_list_exactly_the_new_and_changed_actions() -> None:
    text = DOC.read_text(encoding="utf-8")
    new_part = text.split("## Новые действия")[1].split("## Изменённые")[0]
    assert set(re.findall(r"^\| `([a-z_]+\.[a-z_]+)` \|", new_part, re.M)) == set(w5.WAVE5_IDS)
    for aid, (tier, _spends) in EXPECTED_TIER.items():
        assert re.search(rf"\| `{re.escape(aid)}` \| {tier} \|", new_part), aid
    changed = text.split("## Изменённые")[1].split("## Деньги")[0]
    assert set(re.findall(r"`([a-z_]+\.[a-z_]+)`", changed)) == set(w5.WAVE5_CHANGED_IDS)
    for aid in w5.WAVE5_CHANGED_IDS:
        assert registry.find(aid) is not None, aid


def test_publication_is_one_destructive_human_approved_action_and_kill_switch_release_stays_out() -> None:
    ids = {s.id for s in registry.list()}
    # D15: publish.send is the ONLY publication action; it is destructive, irreversible and always needs approval
    assert [i for i in ids if "publish" in i or "publication" in i] == ["publish.send"]
    spec = registry.get("publish.send")
    assert spec.risk == "destructive" and spec.destructive and not spec.reversible and spec.needs_approval
    assert "not_managed" in {p.id for p in spec.preconditions}
    assert "publish_enabled" in {p.id for p in spec.preconditions}
    assert not [i for i in ids if i.startswith(("workflow.", "canvas.", "kill_switch.release",
                                                "kill_switch.set"))]
    assert "project.delete" not in ids
    assert "step.run_step" in ids
    assert "publish" not in registry.get("step.run_step").params_schema["properties"][
        "step_code"]["enum"]
    for name, reason in w5.EXCLUDED_CONTROLS.items():
        assert CYR.search(reason) and len(reason) > 20, name
    excluded_text = DOC.read_text(encoding="utf-8").split("## Осознанные исключения")[1]
    for name in w5.EXCLUDED_CONTROLS:
        assert name in excluded_text, name


def test_wave5_param_names_are_labelled_in_the_ui_or_listed_for_the_integrator() -> None:
    """Подписи параметров живут в web/src/lib/action-layer.ts (граница W5-6): тест фиксирует,
    каких подписей там не хватает, чтобы интегратор их добавил."""
    src = (Path(__file__).resolve().parents[1] / "web" / "src" / "lib" / "action-layer.ts"
           ).read_text(encoding="utf-8")
    labelled = set(re.findall(r"^\s{2}([A-Za-z_0-9]+): \{ label:", src, re.M))
    names = {k for aid in (*w5.WAVE5_IDS, *w5.WAVE5_CHANGED_IDS)
             for k in registry.get(aid).params_schema["properties"]}
    missing = sorted(names - labelled - {"project_id"})
    assert set(missing) <= {"ceiling_credits", "hitl_id", "topic", "topics", "hero_mode",
                            "aspect_ratio", "snapshot_id", "count", "force"}, missing


# --- HITL ----------------------------------------------------------------------------------------------

async def _hitl(env, pid, kind=HITLKind.approve_images):
    async with env.factory() as s:
        frame = (await s.execute(select(Frame).where(Frame.project_id == pid))).scalars().first()
        row = HITLRequest(project_id=pid, frame_id=frame.id, kind=kind, payload={"x": 1},
                          decision=HITLDecision.pending)
        s.add(row)
        await s.commit()
        return row.id, frame.id


async def _decision(env, hitl_id):
    async with env.factory() as s:
        return (await s.get(HITLRequest, hitl_id)).decision


async def test_hitl_list_then_approve_runs_the_real_endpoint(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    hid, fid = await _hitl(env, pid)
    listed = await _run(env, [step("hitl.list_pending", project_id=pid)])
    out = listed["steps"][0]["result"]
    assert out["count"] == 1 and out["requests"][0]["hitl_id"] == hid

    pv = await _preview(env, [step("hitl.approve", project_id=pid, hitl_id=hid)])
    assert pv.status_code == 200, pv.text
    assert pv.json()["preview"]["needs_approval"] is True
    # без подтверждения решение не принимается
    plan = pv.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code in (403, 409)
    assert await _decision(env, hid) == HITLDecision.pending

    done = await _run(env, [step("hitl.approve", project_id=pid, hitl_id=hid)], approve=True)
    assert done["status"] == "succeeded", done
    assert await _decision(env, hid) == HITLDecision.approved
    async with env.factory() as s:
        assert (await s.get(Frame, fid)).status == FrameStatus.image_approved
    # повторное решение отклоняется условием
    again = await _preview(env, [step("hitl.approve", project_id=pid, hitl_id=hid)])
    assert again.status_code == 422 and "hitl_pending" in str(again.json())


async def test_hitl_reject_marks_the_frame_failed(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    hid, fid = await _hitl(env, pid)
    done = await _run(env, [step("hitl.reject", project_id=pid, hitl_id=hid)], approve=True)
    assert done["status"] == "succeeded", done
    assert await _decision(env, hid) == HITLDecision.rejected
    async with env.factory() as s:
        assert (await s.get(Frame, fid)).status == FrameStatus.failed


async def test_hitl_regenerate_needs_a_ceiling_and_binds_approval_to_it(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    hid, fid = await _hitl(env, pid)
    bare = await _preview(env, [step("hitl.regenerate", project_id=pid, hitl_id=hid)])
    assert bare.status_code in (400, 422)  # потолок обязателен в схеме
    ok = await _preview(env, [step("hitl.regenerate", project_id=pid, hitl_id=hid,
                                   ceiling_credits=30)])
    assert ok.status_code == 200, ok.text
    assert ok.json()["preview"]["cost"]["high"] == 30 and ok.json()["preview"]["needs_approval"]
    done = await _run(env, [step("hitl.regenerate", project_id=pid, hitl_id=hid,
                                 ceiling_credits=30)], approve=True)
    assert done["status"] == "succeeded", done
    assert await _decision(env, hid) == HITLDecision.regenerate
    async with env.factory() as s:
        assert (await s.get(Frame, fid)).status == FrameStatus.image_prompt_ready


async def test_hitl_wrong_project_or_missing_request_is_refused(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    other = await env.add_project(frames=1) if False else pid + 99
    hid, _ = await _hitl(env, pid)
    r = await _preview(env, [step("hitl.reject", project_id=pid, hitl_id=hid + 50)])
    assert r.status_code == 422 and "hitl_pending" in str(r.json())
    r = await _preview(env, [step("hitl.reject", project_id=other, hitl_id=hid)])
    assert r.status_code in (404, 422)


# --- проект ---------------------------------------------------------------------------------------------

async def test_project_create_and_child_use_the_real_endpoints(env, not_managed) -> None:
    made = await _run(env, [step("project.create", title="  Урок про кофе  ", topic="кофе",
                                 hero_mode="no_hero")])
    assert made["status"] == "succeeded", made
    out = made["steps"][0]["result"]
    assert out["project_id"] and out["title"] == "Урок про кофе"
    async with env.factory() as s:
        row = await s.get(Project, out["project_id"])
        assert row is not None and row.topic == "кофе" and row.auto_mode is False
    child = await _run(env, [step("project.create_child", project_id=out["project_id"])])
    assert child["status"] == "succeeded", child
    assert child["steps"][0]["result"]["parent_id"] == out["project_id"]
    blank = await _preview(env, [step("project.create", title="   ")])
    assert blank.status_code == 422 and "title_nonblank" in str(blank.json())


async def test_patch_settings_changes_only_named_fields_and_checks_the_answer(env,
                                                                              not_managed) -> None:
    pid = await env.add_project(frames=1)
    none = await _preview(env, [step("project.patch_settings", project_id=pid)])
    assert none.status_code == 422 and "settings_given" in str(none.json())
    done = await _run(env, [step("project.patch_settings", project_id=pid, title="Новое имя",
                                 aspect_ratio="16:9")])
    assert done["status"] == "succeeded", done
    assert done["steps"][0]["result"]["updated"] == ["aspect_ratio", "title"]
    async with env.factory() as s:
        row = await s.get(Project, pid)
        assert row.title == "Новое имя" and row.aspect_ratio == "16:9"
    bad = await _run(env, [step("project.patch_settings", project_id=pid,
                                image_generator="no_such_model")])
    assert bad["status"] == "failed" and "генератор" in str(bad["steps"][0]["error"]).lower()


async def test_massfactory_start_needs_ceiling_and_stays_behind_approval(env, not_managed,
                                                                         stub_writes) -> None:
    pid = await env.add_project(frames=1)
    stub_writes["patterns"][r"/mass-lanes/start$"] = {"started_id": 77, "queue_size": 2,
                                                      "remaining": 1}
    no = await _preview(env, [step("massfactory.start", project_id=pid, topics=["а", "б"])])
    assert no.status_code in (400, 422)
    ok = await _preview(env, [step("massfactory.start", project_id=pid, topics=["а", "б"],
                                   ceiling_credits=150)])
    assert ok.status_code == 200, ok.text
    assert ok.json()["preview"]["cost"]["high"] == 150
    plan = ok.json()["plan_id"]
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code in (403, 409)
    assert stub_writes["calls"] == []
    # потолок выше потолка расходов сессии слоя подтвердить нельзя
    huge = await _preview(env, [step("massfactory.start", project_id=pid, topics=["а", "б"],
                                     ceiling_credits=5000)])
    assert huge.status_code == 200 and huge.json()["preview"]["budget"]["fits"] is False
    assert (await env.client.post(f"{BASE}/plans/{huge.json()['plan_id']}/approve")
            ).status_code == 409
    done = await _run(env, [step("massfactory.start", project_id=pid, topics=["а", "б"],
                                 ceiling_credits=150)], approve=True)
    assert done["status"] == "succeeded", done
    assert stub_writes["calls"] == [("POST", f"/projects/{pid}/mass-lanes/start", None,
                                     {"topics": ["а", "б"]})]
    assert done["steps"][0]["result"]["started_id"] == 77


# --- аварийный стоп ---------------------------------------------------------------------------------------

async def test_kill_switch_engage_goes_through_the_kill_switch_endpoint(env, not_managed, stub_writes) -> None:
    """The endpoint call is STUBBED: this test must never engage the kill switch of the real database."""
    pid = await env.add_project(frames=1)
    stub_writes["patterns"][r"/observations/kill-switch$"] = {"active": True}
    short = await _preview(env, [step("kill_switch.engage", project_id=pid, rationale="ab")])
    assert short.status_code in (400, 422)
    done = await _run(env, [step("kill_switch.engage", project_id=pid,
                                 rationale="остановить расход до проверки")])
    assert done["status"] == "succeeded", done
    assert done["steps"][0]["result"] == {"active": True}
    assert [c[:2] for c in stub_writes["calls"]] == [("POST", f"/projects/{pid}/observations/kill-switch")]


# --- загрузки -----------------------------------------------------------------------------------------------

def _xlsx_bytes() -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "план"
    ws.append(["№", "Закадр"])
    ws.append([1, "Привет"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _b64(data: bytes) -> str:
    import base64

    return base64.b64encode(data).decode()


async def test_upload_xlsx_goes_through_the_upload_endpoint(env, not_managed, stub_writes) -> None:
    pid = await env.add_project(frames=1)
    stub_writes["patterns"][r"/xlsx/upload$"] = {"status": "script_ready"}
    done = await _run(env, [step("project.upload_xlsx", project_id=pid, filename="plan.xlsx",
                                 data_base64=_b64(_xlsx_bytes()))], approve=True)
    assert done["status"] == "succeeded", done
    assert [c[:2] for c in stub_writes["calls"]] == [("POST", f"/projects/{pid}/xlsx/upload")]
    bad = await _preview(env, [step("project.upload_xlsx", project_id=pid, filename="plan.xlsx",
                                    data_base64=_b64("это не xlsx".encode()))])
    assert bad.status_code == 422 and "upload_source" in str(bad.json())
    exe = await _preview(env, [step("project.upload_xlsx", project_id=pid, filename="run.exe",
                                    data_base64=_b64(b"MZ"))])
    assert exe.status_code == 422


async def test_hero_replace_writes_the_character_file(env, not_managed) -> None:
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00"
           b"\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7"
           b"5\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")
    pid = await env.add_project(frames=1)
    done = await _run(env, [step("hero.replace_image", project_id=pid, filename="hero1.png",
                                 data_base64=_b64(png))], approve=True)
    assert done["status"] == "succeeded", done
    path = Path(done["steps"][0]["result"]["path"])
    assert path.name == "hero1.png" and path.read_bytes() == png


async def test_storage_upload_and_clear_use_the_node_endpoints(env, not_managed,
                                                               stub_writes) -> None:
    pid = await env.add_project(frames=1)
    async with env.factory() as s:
        p = await s.get(Project, pid)
        p.meta = {"canvas_graph": {"nodes": [{"id": "st1", "type": "storage", "data": {}}],
                                   "edges": []}}
        await s.commit()
    stub_writes["patterns"][r"/storage/st1/upload$"] = {"ok": True, "fileName": "upload_x.txt"}
    stub_writes["patterns"][r"/storage/st1/files$"] = {"ok": True, "removed": 3}
    up = await _run(env, [step("storage.upload_file", project_id=pid, node_key="st1",
                               filename="note.txt", data_base64=_b64("заметка".encode()))])
    assert up["status"] == "succeeded", up
    assert up["steps"][0]["result"]["file_name"] == "upload_x.txt"
    wipe = await _preview(env, [step("storage.clear_files", project_id=pid, node_key="st1")])
    assert wipe.status_code == 200 and wipe.json()["preview"]["needs_approval"] is True
    cleared = await _run(env, [step("storage.clear_files", project_id=pid, node_key="st1")],
                         approve=True)
    assert cleared["steps"][0]["result"]["removed"] == 3
    assert [c[0] for c in stub_writes["calls"]] == ["POST", "DELETE"]
    bad = await _preview(env, [step("storage.clear_files", project_id=pid, node_key="../x")])
    assert bad.status_code in (400, 422)
