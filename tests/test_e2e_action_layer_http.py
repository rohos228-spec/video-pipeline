"""Сквозная проверка командной строки через HTTP-API (без сети и без платных вызовов).

Путь каждого сценария один и тот же, как у оператора в интерфейсе:
``POST /plans/from-text`` (модель - заглушка транспорта) -> превью -> ``approve`` (одноразовая серверная запись,
привязанная к хэшу плана) -> ``run`` -> опрос ``GET /plans/{id}`` -> ``GET /history``.

Проект - реалистичный сид ``scripts/eval_seed.py`` (30 кадров, файлы, очередь, ноды, предложения изменений).
Платное и разрушающее исполняют фейковые ``test.*`` действия из ``scripts/e2e_action_layer_fake_paid.py``
(в памяти, без сети). Проверяется не качество модели, а всё вокруг неё: превью, подтверждение, потолок
кредитов, аварийный стоп, вложения, устаревший план, чужой проект, журнал.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text, update

from app.models import Frame, Project
from app.services import kill_switch, prompt_library
from app.services.action_layer import executor, planner, store
from app.services.action_layer import attachments as att
from app.services.action_layer.spec import ensure_builtin_actions, registry
from app.settings import settings
from tests.action_layer_support import env  # noqa: F401
from tests.test_action_layer_planner import reply, res
from tests.test_action_planner_seed_context import load_seed

BASE = "/api/action-layer"
PID, OTHER = 2, 3
MP3 = b"ID3" + bytes(300)
PNG = b"\x89PNG\r\n\x1a\n" + bytes(300)
_LAUNCHER = Path(__file__).resolve().parents[1] / "scripts" / "e2e_action_layer_fake_paid.py"


def st(aid: str, deps=None, **params) -> dict:
    return {"action_id": aid, "params": {"project_id": PID, **params}, "depends_on": deps or []}


def fake_st(aid: str, deps=None, **params) -> dict:
    """Шаг фейкового действия: у них нет project_id в схеме."""
    return {"action_id": aid, "params": params, "depends_on": deps or []}


class Model:
    """Заглушка модели планировщика: отдаёт заранее заданный ответ и запоминает запросы."""

    def __init__(self, *steps, clar: str | None = None, expl: str = "Делаю.") -> None:
        self.result = res(reply(list(steps), clar=clar, expl=expl))
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def use(*steps, **kw) -> Model:
    model = Model(*steps, **kw)
    planner.set_transport(model)
    return model


async def ask(env, text: str, *, project_id: int | None = PID, attachments: list[str] | None = None):
    body: dict = {"text": text}
    if project_id is not None:
        body["project_id"] = project_id
    if attachments:
        body["attachments"] = attachments
    return await env.client.post(f"{BASE}/plans/from-text", json=body)


async def plan_view(env, plan_id: str) -> dict:
    r = await env.client.get(f"{BASE}/plans/{plan_id}")
    assert r.status_code == 200, r.text
    return r.json()


async def approve(env, plan_id: str):
    return await env.client.post(f"{BASE}/plans/{plan_id}/approve")


async def run(env, plan_id: str):
    return await env.client.post(f"{BASE}/plans/{plan_id}/run")


async def finish(env, plan_id: str) -> dict:
    """approve (если нужно) -> run -> ждать конца -> вид плана; каждый шаг проверяется по кодам HTTP."""
    pv = await plan_view(env, plan_id)
    needs = pv.get("needs_approval")
    if needs is None:
        needs = pv.get("risk_max") in ("paid", "destructive")
    if needs:
        a = await approve(env, plan_id)
        assert a.status_code == 200, a.text
    r = await run(env, plan_id)
    assert r.status_code == 200 and r.json()["status"] == "running", r.text
    await executor.wait(plan_id)
    return await plan_view(env, plan_id)


async def history(env, plan_id: str) -> list[dict]:
    r = await env.client.get(f"{BASE}/history", params={"plan_id": plan_id, "limit": 200})
    assert r.status_code == 200, r.text
    return list(reversed(r.json()["items"]))  # старые первыми


def events(rows: list[dict]) -> list[str]:
    return [r["event"] for r in rows]


# --- окружение ------------------------------------------------------------------------------------------

def _load_launcher():
    spec = importlib.util.spec_from_file_location("e2e_http_fake_launcher", _LAUNCHER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest_asyncio.fixture
async def live(env, tmp_path, monkeypatch):  # noqa: F811
    ensure_builtin_actions()
    monkeypatch.setattr(prompt_library, "PROMPTS_ROOT", prompt_library.PROMPTS_ROOT)
    monkeypatch.setattr(settings, "vibecode_api_key", "vk-test-key")
    monkeypatch.setattr(settings, "vibecode_base_url", "https://vibecode.moe/v1")
    monkeypatch.setattr(settings, "action_layer_planner_max_catalog", 24)
    monkeypatch.setattr(settings, "action_layer_planner_calls_per_hour", 10000)
    monkeypatch.setattr(settings, "spend_project_cap_usd", 1000.0)
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 1000.0)
    monkeypatch.setattr(att, "_last_cleanup", 0.0)
    monkeypatch.setenv("ACTION_LAYER_TEST_ACTIONS", "1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed = await load_seed().seed_realistic(project_id=PID, factory=env.factory,
                                            prompts_dir=tmp_path / "prompts")
    async with env.factory() as s:  # второй проект: чужие вложения и чужие шаги
        s.add(Project(id=OTHER, topic="other", slug="other-proj"))
        await s.commit()
    env.seed = seed
    yield env
    planner.set_transport(None)
    kill_switch.reset_cache()


@pytest.fixture
def fake(live):
    """Фейковые платные/разрушающие действия в общем реестре (на время теста)."""
    mod = _load_launcher()
    mod.JOBS.clear()
    added = []
    for spec in mod.build_fake_specs():
        spec = dataclasses.replace(spec, poll_interval_s=0.02)
        registry.register(spec)
        added.append(spec.id)
    mod.events = lambda: ([json.loads(x) for x in (live.tmp_path / "fake_jobs.jsonl").read_text("utf-8")
                           .splitlines()] if (live.tmp_path / "fake_jobs.jsonl").exists() else [])
    yield mod
    for aid in added:
        registry.unregister(aid)


def pad_clips(env, *numbers: int) -> None:
    """Заглушки видео сида слишком малы для проверки «новый файл готов» (>= 1 КБ): дополняем нулями."""
    for n in numbers:
        for clip in (env.seed.data_dir / "videos").glob(f"clip_{n:03d}*.mp4"):
            clip.write_bytes(clip.read_bytes() + bytes(2048))


async def frame_state(env, number: int) -> dict:
    async with env.factory() as s:
        f = (await s.scalars(select(Frame).where(Frame.project_id == PID, Frame.number == number))).one()
        return {"image_prompt": f.image_prompt, "animation_prompt": f.animation_prompt,
                "attrs": json.loads(json.dumps(f.attrs or {}, sort_keys=True))}


# --- 1. чтение ----------------------------------------------------------------------------------------

async def test_read_action_runs_without_approval_and_is_in_history(live) -> None:
    model = use(st("montage.get_board"), expl="Читаю доску.")
    r = await ask(live, "покажи состояние доски монтажа")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preview"]["ok"] and body["preview"]["needs_approval"] is False
    assert body["preview"]["risk_max"] == "read" and body["needs_clarification"] is None
    assert len(model.calls) == 1
    pid = body["plan_id"]
    assert (await plan_view(live, pid))["status"] == "proposed"      # планировщик ничего не запускает
    view = await finish(live, pid)
    assert view["status"] == "succeeded" and view["spent_credits"] == 0.0
    board = view["steps"][0]["result"]
    assert board["frame_count"] == 30 and len(board["frames"]) == 30
    assert board["pending_ops"]                                     # очередь правок сида видна
    rows = await history(live, pid)
    assert events(rows)[0] == "plan_created" and events(rows)[-1] == "plan_finished"
    assert {"run_requested", "plan_started", "step_started", "step_succeeded"} <= set(events(rows))
    assert not {"approval_created", "approval_consumed"} & set(events(rows))   # чтению подтверждение не нужно
    assert rows[0]["request_text"] == "покажи состояние доски монтажа"
    allrows = (await live.client.get(f"{BASE}/history", params={"limit": 50})).json()["items"]
    assert any(r["event"] == "planner_call" for r in allrows)       # вызов модели тоже в журнале


# --- 2. локальная запись и обратимость ---------------------------------------------------------------------

async def test_local_safe_write_changes_db_and_is_reversed_by_repeating_it(live) -> None:
    pad_clips(live, 4)
    before = await frame_state(live, 4)
    use(st("montage.swap_shots", frame_number=4, kind="both"), expl="Меняю шоты.")
    r = await ask(live, "поменяй местами шот 1 и шот 2 в кадре 4")
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    (step,) = pv["steps"]
    assert step["risk"] == "local_safe" and step["reversible"] is True and not pv["needs_approval"]

    view = await finish(live, r.json()["plan_id"])
    assert view["status"] == "succeeded", [(x["status"], x["error"]) for x in view["steps"]]
    assert view["steps"][0]["result"]["prompts_swapped"] is True
    after = await frame_state(live, 4)
    assert after != before                                           # база действительно изменилась
    assert before["attrs"]["image_prompt_shot2"] == "финальный план кадра 4" and not before["image_prompt"]
    assert after["image_prompt"] == "финальный план кадра 4" and "image_prompt_shot2" not in after["attrs"]

    # В реестре у действия нет «обратного плана»: обратимость = то же действие ещё раз (описание действия).
    assert registry.get("montage.swap_shots").reversible is True
    use(st("montage.swap_shots", frame_number=4, kind="both"))
    r2 = await ask(live, "верни шоты кадра 4 обратно")
    view2 = await finish(live, r2.json()["plan_id"])
    assert view2["status"] == "succeeded"
    back = await frame_state(live, 4)                                # содержимое вернулось (пустое None -> "")
    assert back["attrs"] == before["attrs"] and not back["image_prompt"] and not back["animation_prompt"]


# --- 3. платное: потолок кредитов ----------------------------------------------------------------------

async def test_paid_action_needs_approval_and_spends_actual_credits(live, fake) -> None:
    use(fake_st("test.fake_paid_instant", label="проверка"), expl="Тестовая платная задача.")
    r = await ask(live, "тест платное мгновенное действие")
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    assert pv["needs_approval"] and pv["risk_max"] == "paid" and pv["cost"]["high"] == 50.0
    pid = r.json()["plan_id"]
    assert (await run(live, pid)).status_code == 403                 # без подтверждения не запускается
    assert fake.events() == []
    view = await finish(live, pid)
    assert view["status"] == "succeeded" and view["spent_credits"] == pytest.approx(42.0)
    assert [e["event"] for e in fake.events()] == ["instant_paid"]
    rows = await history(live, pid)
    assert events(rows)[:4] == ["plan_created", "approval_created", "approval_consumed", "run_requested"][:1] \
        + events(rows)[1:4]
    assert {"approval_created", "approval_consumed"} <= set(events(rows))


async def test_session_credit_cap_refuses_a_paid_plan_before_anything_runs(live, fake, monkeypatch) -> None:
    monkeypatch.setattr(settings, "action_layer_session_cap_credits", 40.0)       # верхняя оценка 50 > 40
    use(fake_st("test.fake_paid_instant", label="a"), expl="Платное.")
    r = await ask(live, "тест платное мгновенное действие")
    assert r.status_code == 200
    pv = r.json()["preview"]
    assert pv["budget"]["fits"] is False and pv["budget"]["cap_credits"] == 40.0
    pid = r.json()["plan_id"]
    a = await approve(live, pid)
    assert a.status_code == 409 and a.json()["detail"]["code"] == "over_budget"
    assert (await run(live, pid)).status_code == 403
    assert fake.events() == []                                       # ни один фейковый платный вызов не сделан
    assert (await plan_view(live, pid))["status"] == "proposed"


async def test_cap_lowered_after_approval_blocks_the_step_at_run_time(live, fake, monkeypatch) -> None:
    use(fake_st("test.fake_paid_instant", label="b"), expl="Платное.")
    pid = (await ask(live, "тест платное мгновенное действие")).json()["plan_id"]
    assert (await approve(live, pid)).status_code == 200            # потолок по умолчанию 200: влезает
    monkeypatch.setattr(settings, "action_layer_session_cap_credits", 40.0)       # потом потолок снизили
    assert (await run(live, pid)).status_code == 200
    await executor.wait(pid)
    view = await plan_view(live, pid)
    assert view["status"] == "failed" and view["steps"][0]["status"] == "failed"
    assert fake.events() == []                                       # шаг не стартовал
    assert "budget_block" in events(await history(live, pid))


async def test_two_paid_steps_do_not_fit_one_plan_cap(live, fake, monkeypatch) -> None:
    monkeypatch.setattr(settings, "action_layer_session_cap_credits", 60.0)
    use(fake_st("test.fake_paid_instant", label="1"), fake_st("test.fake_paid_instant", deps=[0], label="2"))
    r = await ask(live, "тест платное мгновенное действие два раза")
    pv = r.json()["preview"]
    assert pv["cost"]["high"] == 100.0 and pv["budget"]["fits"] is False
    a = await approve(live, r.json()["plan_id"])
    assert a.status_code == 409 and a.json()["detail"]["code"] == "over_budget"


# --- 4. разрушающее: подтверждение одноразовое -------------------------------------------------------------

async def test_destructive_needs_approval_and_the_same_approval_cannot_be_replayed(live, fake) -> None:
    use(fake_st("test.fake_destructive", label="x"), expl="Стираю тестовую запись.")
    r = await ask(live, "тест стирающее действие")
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    assert pv["needs_approval"] and pv["risk_max"] == "destructive" and pv["destructive_effects"]
    pid = r.json()["plan_id"]
    assert (await run(live, pid)).status_code == 403                 # без подтверждения нельзя
    assert fake.events() == []

    a = await approve(live, pid)
    assert a.status_code == 200
    approval = a.json()
    assert approval["plan_id"] == pid and len(approval["plan_hash"]) == 64
    assert approval["plan_hash"] == pv["plan_hash"]                  # привязано к хэшу плана
    assert approval["operator"] == "local_web_operator"
    assert (await run(live, pid)).status_code == 200
    await executor.wait(pid)
    assert (await plan_view(live, pid))["status"] == "succeeded"
    assert [e["event"] for e in fake.events()] == ["destructive_done"]

    again = await run(live, pid)                                     # повтор того же плана тем же подтверждением
    assert again.status_code == 409 and again.json()["detail"]["code"] == "plan_finished"
    assert len(fake.events()) == 1                                   # второго стирания не было

    # Новый план с тем же содержимым: старое подтверждение к нему не относится.
    use(fake_st("test.fake_destructive", label="x"), expl="Стираю тестовую запись.")
    pid2 = (await ask(live, "тест стирающее действие")).json()["plan_id"]
    assert pid2 != pid
    denied = await run(live, pid2)
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "approval_required"
    assert len(fake.events()) == 1
    rows = await history(live, pid)
    assert events(rows).count("approval_consumed") == 1


async def test_run_montage_deletes_the_old_final_so_it_needs_approval(live) -> None:
    use(st("montage.run_montage"), expl="Запускаю монтаж.")
    r = await ask(live, "запусти монтаж")
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    assert pv["ok"] and pv["needs_approval"] and pv["risk_max"] == "destructive"
    assert pv["cost"]["high"] == 0 and pv["steps"][0]["spends_money"] is False
    effect = pv["destructive_effects"][0]["text"]
    assert "final.mp4" in effect and "сборка займёт время" in effect and "платных вызовов нет" in effect
    assert (await run(live, r.json()["plan_id"])).status_code == 403   # без подтверждения не запускается


async def test_assemble_macro_shows_one_plan_with_one_approval(live) -> None:
    r = await live.client.post(f"{BASE}/plans/from-macro", json={
        "macro_id": "smart.assemble_video", "params": {"project_id": PID}})
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    assert [s["action_id"] for s in pv["steps"]] == ["montage.align_audio", "montage.run_montage"]
    assert pv["needs_approval"] and pv["risk_max"] == "destructive" and pv["cost"]["high"] == 0
    assert [e["action_id"] for e in pv["destructive_effects"]] == ["montage.run_montage"]
    assert (await run(live, r.json()["plan_id"])).status_code == 403


async def test_plan_changed_after_approval_is_refused(live, fake) -> None:
    use(fake_st("test.fake_destructive", label="before"), expl="Стираю.")
    pid = (await ask(live, "тест стирающее действие")).json()["plan_id"]
    assert (await approve(live, pid)).status_code == 200
    async with live.factory() as s:                                  # кто-то подменил параметры шага в базе
        await s.execute(update(store.ActionPlanStep).where(store.ActionPlanStep.plan_id == pid)
                        .values(params={"label": "after"}))
        await s.commit()
    r = await run(live, pid)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "plan_changed", r.text
    assert fake.events() == []


# --- 5. аварийный стоп -------------------------------------------------------------------------------------

async def test_kill_switch_refuses_planning_and_execution_until_switched_off(live) -> None:
    pad_clips(live, 8)
    use(st("montage.swap_shots", frame_number=8, kind="both"))
    before = await frame_state(live, 8)
    r = await ask(live, "поменяй шоты кадра 8")
    pid = r.json()["plan_id"]                                        # превью создано до включения стопа
    await kill_switch.set_state(live.factory, active=True, actor="test", rationale="проверка e2e")
    try:
        denied = await run(live, pid)
        assert denied.status_code == 409 and denied.json()["detail"]["code"] == "kill_switch_active"
        assert await frame_state(live, 8) == before                  # база не тронута
        assert (await plan_view(live, pid))["status"] == "proposed"
        planning = await ask(live, "покажи доску")
        assert planning.status_code == 409 and planning.json()["detail"]["code"] == "kill_switch_active"
    finally:
        await kill_switch.set_state(live.factory, active=False, actor="test", rationale="снято")
    view = await finish(live, pid)                                   # стоп снят: тот же план проходит
    assert view["status"] == "succeeded"
    assert await frame_state(live, 8) != before


async def test_kill_switch_stops_a_running_background_step(live, fake) -> None:
    use(fake_st("test.fake_paid_job", label="j", seconds=30), expl="Фоновая тестовая задача.")
    pid = (await ask(live, "тест платная фоновая задача")).json()["plan_id"]
    assert (await approve(live, pid)).status_code == 200
    assert (await run(live, pid)).status_code == 200
    for _ in range(100):                                             # дождаться живого шага
        if any(e["event"] == "started" for e in fake.events()):
            break
        await asyncio.sleep(0.03)
    await kill_switch.set_state(live.factory, active=True, actor="test", rationale="остановить задачу")
    try:
        await executor.wait(pid)
    finally:
        await kill_switch.set_state(live.factory, active=False, actor="test", rationale="снято")
    view = await plan_view(live, pid)
    assert view["status"] == "stopped" and view["steps"][0]["status"] == "cancelled"
    assert {e["event"] for e in fake.events()} == {"started", "stopped"}      # фоновая задача остановлена, не дошла
    assert "step_cancelled" in events(await history(live, pid))


# --- 6. вложения и загрузка ---------------------------------------------------------------------------------

async def upload(env, name: str, data: bytes, ctype: str, project_id: int = PID):
    return await env.client.post(f"{BASE}/attachments", data={"project_id": str(project_id)},
                                 files={"file": (name, data, ctype)})


async def test_attachment_then_upload_action_end_to_end(live) -> None:
    up = await upload(live, "voice.mp3", MP3, "audio/mpeg")
    assert up.status_code == 200, up.text
    att_id = up.json()["attachment_id"]
    assert up.json()["sha256"] == hashlib.sha256(MP3).hexdigest()

    model = use(st("montage.upload_voice"), expl="Загружаю озвучку.")      # модель путь не знает и не пишет
    r = await ask(live, "загрузи озвучку", attachments=[att_id])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["attachments_used"] == [att_id] and body["preview"]["ok"]
    pv = body["preview"]
    assert pv["needs_approval"] and pv["risk_max"] == "destructive"          # заменяет озвучку: нужно подтверждение
    prompt = json.dumps(json.loads(model.calls[0]["body"]["messages"][1]["content"]), ensure_ascii=False)
    assert "attachments/" not in prompt and str(live.tmp_path) not in prompt   # модель не видит пути
    pid = body["plan_id"]
    step = (await plan_view(live, pid))["steps"][0]
    assert step["params"]["source_path"] == f"attachments/{PID}/{att_id}_voice.mp3"

    view = await finish(live, pid)
    assert view["status"] == "succeeded", view
    result = view["steps"][0]["result"]
    assert result["uploaded_sha256"] == hashlib.sha256(MP3).hexdigest()
    async with live.factory() as s:
        project = await s.get(Project, PID)
        meta = project.meta or {}
        ddir = project.data_dir
    assert (Path(ddir) / "audio" / "voice_full.mp3").read_bytes() == MP3     # файл реально лёг в проект
    assert str(meta["montage_voice_path"]).endswith("voice_full.mp3")

    # Файл использован: второй план с ним не строится.
    use(st("montage.upload_voice"))
    again = await ask(live, "загрузи озвучку", attachments=[att_id])
    assert again.status_code == 409 and again.json()["detail"]["code"] == "attachment_consumed"


async def test_upload_without_a_file_asks_for_it_and_never_builds_a_plan(live) -> None:
    use(st("montage.upload_image", frame_number=3), expl="Загружаю.")
    r = await ask(live, "загрузи картинку в кадр 3")
    body = r.json()
    assert r.status_code == 200 and body["plan_id"] is None and body["preview"] is None
    assert "Прикрепите файл" in body["needs_clarification"]
    assert body["clarification_options"] == ["Прикрепить файл"]
    assert body["attachment_request"]["action_id"] == "montage.upload_image"


async def test_attachment_of_another_project_is_refused(live) -> None:
    up = await upload(live, "voice.mp3", MP3, "audio/mpeg", project_id=OTHER)
    assert up.status_code == 200, up.text
    use(st("montage.upload_voice"))
    r = await ask(live, "загрузи озвучку", attachments=[up.json()["attachment_id"]])
    assert r.status_code == 403 and r.json()["detail"]["code"] == "attachment_wrong_project"


async def test_bad_files_are_rejected_at_upload(live) -> None:
    for name, data, ctype in (("tool.exe", b"MZ" + bytes(50), "application/octet-stream"),
                              ("fake.png", b"not a png", "image/png")):
        r = await upload(live, name, data, ctype)
        assert r.status_code == 415, (name, r.text)
    root = live.tmp_path / "attachments"
    assert not root.exists() or not [p for p in root.rglob("*") if p.is_file()]


# --- 7. устаревший план, чужой проект ----------------------------------------------------------------------------

async def test_stale_plan_is_refused_after_the_project_changed(live, fake) -> None:
    pad_clips(live, 4)
    use(st("montage.swap_shots", frame_number=4, kind="both"))
    safe = (await ask(live, "поменяй шоты кадра 4")).json()["plan_id"]
    use(st("montage.get_board"))
    read_only = (await ask(live, "покажи доску")).json()["plan_id"]
    before = await frame_state(live, 4)
    async with live.factory() as s:                                  # проект изменился: кадр 30 удалён
        await s.execute(delete(Frame).where(Frame.project_id == PID, Frame.number == 30))
        await s.commit()
    r = await run(live, safe)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_context"
    assert await frame_state(live, 4) == before                      # ничего не выполнено
    r = await run(live, read_only)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_context"
    # план строится заново на новом состоянии: теперь проходит
    use(st("montage.swap_shots", frame_number=4, kind="both"))
    fresh = (await ask(live, "поменяй шоты кадра 4")).json()["plan_id"]
    assert (await finish(live, fresh))["status"] == "succeeded"


async def test_stale_destructive_plan_cannot_even_be_approved(live, fake) -> None:
    use(fake_st("test.fake_destructive", label="x"))
    pid = (await ask(live, "тест стирающее действие")).json()["plan_id"]
    async with live.factory() as s:
        await s.execute(delete(Frame).where(Frame.project_id == PID, Frame.number == 30))
        await s.commit()
    a = await approve(live, pid)
    assert a.status_code == 409 and a.json()["detail"]["code"] == "stale_context"
    assert fake.events() == []


async def test_steps_for_another_project_reject_the_whole_plan(live) -> None:
    model = use(st("montage.get_board"), {**st("montage.swap_shots", frame_number=4, kind="both"),
                                          "params": {"project_id": OTHER, "frame_number": 4, "kind": "both"}})
    r = await ask(live, "покажи доску и поменяй шоты кадра 4")
    assert r.status_code == 422, r.text
    d = r.json()["detail"]
    assert d["code"] == "planner_rejected" and d["reason"] == "foreign_project"
    assert len(model.calls) == 1                                     # отказ по безопасности не повторяется
    plans = (await live.client.get(f"{BASE}/plans", params={"project_id": OTHER})).json()["items"]
    assert plans == []


async def test_unknown_project_and_unknown_action_are_refused(live) -> None:
    use(st("montage.get_board"))
    r = await ask(live, "покажи доску", project_id=999)
    assert r.status_code == 404
    use({"action_id": "shell.exec", "params": {"cmd": "rm -rf /"}, "depends_on": []})
    r = await ask(live, "покажи доску")
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "unknown_action"


async def test_a_plan_cannot_carry_approval_fields(live) -> None:
    r = await live.client.post(f"{BASE}/plans/preview", json={
        "steps": [{"action_id": "montage.get_board", "params": {"project_id": PID}}],
        "approval_id": "x", "approved": True})
    assert r.status_code == 422                                      # подтверждение нельзя передать в теле плана
    use(st("montage.get_board"))
    pid = (await ask(live, "покажи доску")).json()["plan_id"]
    r = await live.client.post(f"{BASE}/plans/{pid}/run", json={"approval_id": "x"})
    assert r.status_code == 422


# --- 8. история ------------------------------------------------------------------------------------------------

async def test_history_is_append_only_and_lists_every_request(live, fake) -> None:
    use(fake_st("test.fake_paid_instant", label="h"))
    pid = (await ask(live, "тест платное мгновенное действие")).json()["plan_id"]
    await finish(live, pid)
    rows = await history(live, pid)
    assert events(rows) == ["plan_created", "planner_plan_linked", "approval_created", "approval_consumed",
                            "run_requested", "plan_started", "step_started", "step_succeeded", "plan_finished"]
    assert {r["actor"] for r in rows} <= {"local_web_operator", "system", "executor"} | {rows[0]["actor"]}
    last = rows[-1]["id"]
    async with live.factory() as s:                                  # журнал нельзя править задним числом
        for stmt in ("UPDATE action_layer_history SET event='x' WHERE id=:i",
                     "DELETE FROM action_layer_history WHERE id=:i"):
            with pytest.raises(Exception):
                await s.execute(text(stmt), {"i": last})
                await s.commit()
            await s.rollback()
    all_rows = (await live.client.get(f"{BASE}/history", params={"limit": 500})).json()
    assert any(r["event"] == "planner_call" for r in all_rows["items"])
    listed = (await live.client.get(f"{BASE}/plans", params={"limit": 100})).json()["items"]
    assert pid in {p["id"] for p in listed}
