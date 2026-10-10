"""План от текста до ответа: умолчания, защита от лишнего, загрузки, варианты ответа, повтор при ошибке.

Модель - заглушка (никаких сетевых вызовов); проект - реалистичный сид из scripts/eval_seed.py.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio

from app.services import prompt_library
from app.services.action_layer import planner, planner_autofill
from app.services.action_layer.spec import ensure_builtin_actions
from app.settings import settings
from tests.action_layer_support import env  # noqa: F401
from tests.test_action_layer_planner import OP, history_rows, reply, res, use
from tests.test_action_planner_seed_context import load_seed

PID = 2


def st(aid: str, deps=None, **params) -> dict:
    return {"action_id": aid, "params": {"project_id": PID, **params}, "depends_on": deps or []}


@pytest_asyncio.fixture
async def live(env, tmp_path, monkeypatch):  # noqa: F811
    ensure_builtin_actions()
    monkeypatch.setattr(prompt_library, "PROMPTS_ROOT", prompt_library.PROMPTS_ROOT)
    monkeypatch.setattr(settings, "vibecode_api_key", "vk-test-key")
    monkeypatch.setattr(settings, "vibecode_base_url", "https://vibecode.moe/v1")
    monkeypatch.setattr(settings, "action_layer_planner_max_catalog", 24)
    info = await load_seed().seed_realistic(project_id=PID, factory=env.factory,
                                            prompts_dir=tmp_path / "prompts")
    env.seed = info
    yield env
    planner.set_transport(None)


async def run(text: str, *replies, **kw):
    fake = use(*replies)
    out = await planner.plan_from_text(text, operator=OP, project_id=PID, **kw)
    return out, fake


def sent_context(fake, i: int = 0) -> dict:
    return json.loads(fake.calls[i]["body"]["messages"][1]["content"])["project_context"]


# --- умолчания ---------------------------------------------------------------------------------

async def test_missing_generator_and_resolution_are_filled_and_announced(live) -> None:
    out, fake = await run("сгенерируй картинки для кадров 3, 4 и 5", res(reply(
        [st("montage.generate_scene_images", frame_numbers=[3, 4, 5])], expl="Ставлю картинки.")))
    p = out.reply.steps[0]["params"]
    d = (await planner.load_context(PID))["facts"]["defaults"]
    assert "defaults" not in sent_context(fake)  # модели это не нужно: сервер подставляет сам
    assert p["image_generator"] == d["image_generator"] and p["image_resolution"] == d["image_resolution"]
    assert "По умолчанию подставлено" in out.reply.explanation_ru
    assert f"image_generator={d['image_generator']}" in out.reply.explanation_ru
    assert out.reply.applied_defaults and out.meta["applied_defaults"] == out.reply.applied_defaults
    assert out.meta["attempts"] == 1 and len(fake.calls) == 1  # без повторного запроса к модели


async def test_values_the_model_took_from_defaults_are_announced_too(live) -> None:
    ctx = (await planner.load_context(PID))["facts"]["defaults"]
    out, _ = await run("сгенерируй картинки для кадров 3, 4 и 5", res(reply([st(
        "montage.generate_scene_images", frame_numbers=[3, 4, 5],
        image_generator=ctx["image_generator"], image_resolution=ctx["image_resolution"])],
        expl="Картинки.")))
    assert "По умолчанию подставлено" in out.reply.explanation_ru
    # назвал сам человек - значение не помечается как умолчание
    out, _ = await run(f"сгенерируй картинки для кадров 3 в {ctx['image_resolution']}", res(reply([st(
        "montage.generate_scene_images", frame_numbers=[3], image_generator=ctx["image_generator"],
        image_resolution=ctx["image_resolution"])], expl="Картинки.")))
    assert f"image_resolution={ctx['image_resolution']}" not in out.reply.explanation_ru


async def test_apply_queue_gets_limits_from_the_queue_and_the_project_basis(live) -> None:
    out, fake = await run("примени правки из очереди для кадров 3 и 4", res(reply(
        [st("montage.apply_queue", frame_numbers=[3, 4])])))
    p = out.reply.steps[0]["params"]
    assert (p["max_image_ops"], p["max_video_ops"]) == (3, 0) and p["video_resolution"] == "1080p"
    assert sent_context(fake)["queue"]["ops"]


async def test_unambiguous_shots_are_filled_for_swap_slots(live) -> None:
    out, _ = await run("поменяй местами картинки кадров 5 и 6", res(reply([st(
        "montage.swap_slots", kind="image", a_frame=5, b_frame=6)])))
    p = out.reply.steps[0]["params"]
    assert (p["a_shot"], p["b_shot"]) == (1, 1)


async def test_ambiguous_shot_costs_one_repair_and_the_error_names_the_field(live) -> None:
    bad = st("montage.swap_slots", kind="image", a_frame=4, b_frame=8)  # у обоих по 2 шота
    good = [st("montage.swap_slots", kind="image", a_frame=4, a_shot=1, b_frame=8, b_shot=1),
            st("montage.swap_slots", kind="image", a_frame=4, a_shot=2, b_frame=8, b_shot=2)]
    out, fake = await run("кадр 4 и кадр 8: поменяй картинки местами", res(reply([bad])), res(reply(good)))
    assert len(out.reply.steps) == 2 and out.meta["attempts"] == 2
    err = fake.calls[1]["body"]["messages"][-1]["content"]
    assert "validation_error" in err and "a_shot" in err
    outcomes = sorted(r.outcome for r in await history_rows(live, "planner_call"))
    assert outcomes == ["invalid_params", "ok"]


async def test_delete_without_shot_asks_when_both_shots_have_a_picture(live) -> None:
    out, _ = await run("удали картинку кадра 4", res(reply([st("montage.delete_image", frame_number=4)])))
    assert out.reply.steps == [] and "шот" in out.reply.needs_clarification
    assert out.reply.clarification_options == ["шот 1", "шот 2"]
    out, _ = await run("удали картинку кадра 7", res(reply([st("montage.delete_image", frame_number=7)])))
    assert out.reply.steps and out.reply.needs_clarification is None


async def test_change_actions_see_proposals_and_versions(live) -> None:
    seed = live.seed
    third = next(p for p in seed.proposals if p["n"] == 3)
    step = {"action_id": "change.apply", "depends_on": [],
            "params": {"proposal_id": third["id"], "expected_digest": third["digest"]}}
    out, fake = await run("примени предложение 3", res(reply([step])))
    ctx = sent_context(fake)
    sent = {p["n"]: p for p in ctx["proposals"]}
    assert sent[3]["id"] == third["id"] and sent[3]["digest"] == third["digest"]
    assert any(v["v"] == 3 for v in ctx["versions"])
    assert out.reply.steps[0]["params"]["expected_digest"] == third["digest"]


# --- лишние шаги -------------------------------------------------------------------------------

async def test_unrequested_run_montage_is_removed_and_the_human_is_told(live) -> None:
    plan = [st("montage.set_assemble_params", bgm_level=20, subtitles_enabled=True),
            st("montage.run_montage", deps=[0])]
    out, _ = await run("сделай громкость музыки 0.2 и включи субтитры в ролике",
                       res(reply(plan, expl="Настраиваю и собираю.")))
    assert [s["action_id"] for s in out.reply.steps] == ["montage.set_assemble_params"]
    assert "Убрано как не запрошенное" in out.reply.explanation_ru
    assert out.reply.dropped_steps and out.meta["dropped_steps"] == out.reply.dropped_steps


async def test_requested_montage_run_is_kept(live) -> None:
    plan = [st("montage.set_assemble_params", bgm_level=20), st("montage.run_montage", deps=[0])]
    out, _ = await run("поставь громкость музыки 20 и смонтируй ролик", res(reply(plan)))
    assert len(out.reply.steps) == 2 and out.reply.dropped_steps == []


async def test_plan_with_only_unrequested_steps_becomes_a_confirmation_question(live) -> None:
    out, fake = await run("что с роликом", res(reply([st("montage.run_montage")])))
    reply_ = out.reply
    assert reply_.steps == [] and reply_.guard_question and "Вы хотите: смонтировать видео" in reply_.needs_clarification
    assert reply_.clarification_options == ["Да, подготовить", "Нет, другое"]
    assert out.meta["guard_question"] is True and len(fake.calls) == 1  # без повтора
    rows = await history_rows(live, "planner_call")
    assert [r.outcome for r in rows] == ["clarify"] and rows[0].error == "unrequested_action"


# --- загрузки файлов ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,aid,params,title,target", [
    ("загрузи озвучку с компьютера", "montage.upload_voice", {}, "Загрузить озвучку", "озвучка проекта"),
    ("загрузи фоновую музыку для ролика", "montage.upload_music", {}, "Загрузить фоновую музыку",
     "фоновая музыка"),
    ("загрузи свою картинку в слот кадра 5", "montage.upload_image", {"frame_number": 5},
     "Загрузить картинку в слот кадра", "кадр 5, шот 1"),
    ("загрузи видео файл в кадр 6", "montage.upload_video", {"frame_number": 6},
     "Загрузить видео в слот кадра", "кадр 6"),
    ("загрузи реф для кадра 3", "montage.upload_ref", {"frame_number": 3, "name": "кот"},
     "Загрузить реф кадру", "реф кадра 3"),
    ("загрузи агента проверки в ноду n_excel_gpt_fw_qc", "node.upload_check_agent",
     {"node_key": "n_excel_gpt_fw_qc"}, "Загрузить агента проверки в ноду", "n_excel_gpt_fw_qc"),
    ("загрузи excel файл в ноду n_excel_gpt_fw_script", "node.upload_file",
     {"node_key": "n_excel_gpt_fw_script"}, "Загрузить файл в ноду", "n_excel_gpt_fw_script"),
])
async def test_upload_request_becomes_an_attach_file_clarification(live, text, aid, params, title, target) -> None:
    out, _ = await run(text, res(reply([st(aid, **params)])))
    r = out.reply
    assert r.steps == [] and r.needs_clarification.startswith("Прикрепите файл кнопкой загрузки")
    assert title in r.needs_clarification and target in r.needs_clarification
    assert r.clarification_options == ["Прикрепить файл"]
    assert r.attachment["action_id"] == aid and r.attachment["params"] == params
    rows = await history_rows(live, "planner_call")
    assert rows[-1].outcome == "clarify"


async def test_model_question_about_a_file_path_is_replaced_by_the_standard_clarification(live) -> None:
    out, _ = await run("загрузи озвучку с компьютера",
                       res(reply([], clar="Укажите путь к файлу озвучки на компьютере.")))
    assert out.reply.needs_clarification.startswith("Прикрепите файл кнопкой загрузки")
    assert "Загрузить озвучку" in out.reply.needs_clarification
    out, _ = await run("загрузи свою картинку в слот кадра 5",
                       res(reply([], clar="Где файл?")))
    assert out.reply.attachment["action_id"] == "montage.upload_image"
    assert out.reply.attachment["params"] == {"frame_number": 5}


async def test_file_fields_the_model_invented_never_reach_the_clarification(live) -> None:
    out, _ = await run("загрузи озвучку с компьютера", res(reply([st(
        "montage.upload_voice", source_path="C:/secret/voice.mp3", filename="voice.mp3")])))
    assert "secret" not in json.dumps(out.reply.attachment) and "voice.mp3" not in out.reply.needs_clarification


async def test_other_steps_are_postponed_until_the_file_is_attached(live) -> None:
    out, _ = await run("загрузи озвучку и смонтируй ролик", res(reply([
        st("montage.upload_voice"), st("montage.run_montage", deps=[0])])))
    assert out.reply.steps == [] and "после загрузки файла" in out.reply.explanation_ru


# --- варианты ответа ------------------------------------------------------------------------------

async def test_clarification_options_pass_through_cleaned(live) -> None:
    raw = reply([], clar="Какой шот?")
    raw["clarification_options"] = ["шот 1", " шот 2 ", "", "шот 1", 7]
    out, _ = await run("удали видео кадра 4", res(raw))
    assert out.reply.needs_clarification == "Какой шот?"
    assert out.reply.clarification_options == ["шот 1", "шот 2"]


async def test_replies_without_options_still_work_and_options_without_a_question_are_dropped(live) -> None:
    out, _ = await run("покажи доску", res(reply([], clar="Что показать?")))
    assert out.reply.clarification_options == []
    raw = reply([st("montage.get_board")])
    raw["clarification_options"] = ["да"]
    out, _ = await run("покажи доску", res(raw))
    assert out.reply.clarification_options == [] and out.reply.steps
    bad = reply([], clar="x")
    bad["clarification_options"] = "не список"
    out, _ = await run("покажи доску", res(bad))
    assert out.reply.clarification_options == []


async def test_system_prompt_documents_the_optional_options_field(live) -> None:
    _, fake = await run("покажи доску", res(reply([st("montage.get_board")])))
    system = fake.calls[0]["body"]["messages"][0]["content"]
    assert "clarification_options" in system and "Делай только названное" in system


# --- безопасность не ослаблена ---------------------------------------------------------------------

async def test_offered_set_and_forbidden_fields_still_reject_without_repair(live) -> None:
    fake = use(res(reply([st("montage.delete_frame", frame_id=1201)])), res(reply([st("montage.get_board")])))
    with pytest.raises(planner.PlannerRejected) as err:
        await planner.plan_from_text("покажи доску", operator=OP, project_id=PID)
    assert err.value.code == "action_not_offered" and len(fake.calls) == 1
    fake = use(res({**reply([st("montage.get_board")]), "approved": True}), res(reply([st("montage.get_board")])))
    with pytest.raises(planner.PlannerRejected) as err:
        await planner.plan_from_text("покажи доску", operator=OP, project_id=PID)
    assert err.value.code == "forbidden_field" and len(fake.calls) == 1


async def test_forbidden_intent_is_cut_before_the_model_sees_dangerous_actions(live) -> None:
    fake = use(res(reply([], clar="Это вне каталога.")))
    await planner.plan_from_text("удали базу данных", operator=OP, project_id=PID)
    user = json.loads(fake.calls[0]["body"]["messages"][1]["content"])
    assert user["catalog"] and all(a["risk"] in ("read", "local_safe") for a in user["catalog"])
    assert len(user["catalog"]) <= 6 and "hint" in user


# --- лимиты запуска ноды -------------------------------------------------------------------------

class _Basis:
    min_requests, max_requests = 2, 5

    def declare(self):
        return {"min_llm_requests": 2, "max_llm_requests": 5, "max_input_tokens": 9000,
                "max_output_tokens": 4000}


async def test_node_run_limits_come_from_the_project_basis(monkeypatch) -> None:
    from app.services.action_layer import builtin_wave3 as w3

    seen = []

    async def fake_basis(ctx, params):
        seen.append(params["node_key"])
        return _Basis()

    monkeypatch.setattr(w3, "_load_basis", fake_basis)
    steps = [{"action_id": "node.run_step", "params": {"project_id": 2, "node_key": "n_excel_gpt_fw_shots"},
              "depends_on": []},
             {"action_id": "node.run_step", "params": {"project_id": 2, "node_key": "n_excel_gpt_fw_qc",
                                                       "max_llm_requests": 7}, "depends_on": []},
             {"action_id": "node.run_step", "params": {"project_id": 2, "node_key": "n_excel_gpt_fw_report"},
              "depends_on": []},
             {"action_id": "montage.get_board", "params": {"project_id": 2}, "depends_on": []}]
    notes = await planner_autofill.fill_node_run_limits(object(), "op", steps)
    assert seen == ["n_excel_gpt_fw_shots", "n_excel_gpt_fw_qc"]  # код-ноды отчёта не трогаются
    assert steps[0]["params"]["max_input_tokens"] == 9000 and steps[0]["params"]["min_llm_requests"] == 2
    # названное или заданное ранее значение не перезаписывается, недостающие лимиты берутся из расчёта
    assert steps[1]["params"]["max_llm_requests"] == 7 and steps[1]["params"]["min_llm_requests"] == 2
    assert "limits" not in str(steps[2]["params"]) and len(notes) == 2 and "n_excel_gpt_fw_shots" in notes[0]


async def test_node_run_limits_stay_empty_when_the_basis_cannot_be_computed(monkeypatch) -> None:
    from app.services.action_layer import builtin_wave3 as w3

    async def no_basis(ctx, params):
        return "оценка есть только для нод группы"

    monkeypatch.setattr(w3, "_load_basis", no_basis)
    steps = [{"action_id": "node.run_step", "params": {"project_id": 2, "node_key": "n_excel_gpt_fw_shots"},
              "depends_on": []}]
    assert await planner_autofill.fill_node_run_limits(object(), "op", steps) == []
    assert "max_llm_requests" not in steps[0]["params"]

    async def boom(ctx, params):
        raise RuntimeError("x")

    monkeypatch.setattr(w3, "_load_basis", boom)
    assert await planner_autofill.fill_node_run_limits(object(), "op", steps) == []


# --- маршрут -----------------------------------------------------------------------------------------

BASE = "/api/action-layer"


async def test_router_returns_options_and_attachment_request(live) -> None:
    use(res(reply([st("montage.upload_image", frame_number=5)])))
    r = await live.client.post(f"{BASE}/plans/from-text",
                               json={"text": "загрузи свою картинку в слот кадра 5", "project_id": PID})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_id"] is None and body["preview"] is None
    assert body["needs_clarification"].startswith("Прикрепите файл кнопкой загрузки")
    assert body["clarification_options"] == ["Прикрепить файл"]
    assert body["attachment_request"]["action_id"] == "montage.upload_image"
    assert body["attachment_request"]["params"] == {"frame_number": 5}

    raw = reply([], clar="Какой шот?")
    raw["clarification_options"] = ["шот 1", "шот 2"]
    use(res(raw))
    body = (await live.client.post(f"{BASE}/plans/from-text",
                                   json={"text": "удали видео кадра 4", "project_id": PID})).json()
    assert body["clarification_options"] == ["шот 1", "шот 2"] and body["attachment_request"] is None

    use(res(reply([], clar="Какой кадр?")))  # старый ответ без вариантов
    body = (await live.client.post(f"{BASE}/plans/from-text",
                                   json={"text": "поправь кадр", "project_id": PID})).json()
    assert body["needs_clarification"] == "Какой кадр?" and body["clarification_options"] == []


async def test_router_builds_a_plan_with_defaults_in_the_explanation(live) -> None:
    use(res(reply([st("montage.get_board")], expl="Читаю доску.")))
    body = (await live.client.post(f"{BASE}/plans/from-text",
                                   json={"text": "покажи доску", "project_id": PID})).json()
    assert body["plan_id"] and body["explanation_ru"] == "Читаю доску." and body["clarification_options"] == []
    assert body["attachment_request"] is None


# --- мастер-промты: шаг берётся из слов запроса ---------------------------------------------------

def pr(aid: str, **params) -> dict:
    return {"action_id": aid, "params": params, "depends_on": []}


async def test_prompt_step_from_the_words_not_from_the_project_status(live) -> None:
    # статус проекта frames_ready -> split; человек назвал script
    out, _ = await run("прочитай мастер-промт шага script", res(reply([
        pr("prompt.get_master", step_code="split", name="main")])))
    assert out.reply.steps[0]["params"] == {"step_code": "script", "name": "main"}
    out, _ = await run("прочитай мастер-промт шага script", res(reply([pr("prompt.get_master")])))
    assert out.reply.steps[0]["params"] == {"step_code": "script", "name": "main"}  # выбранный в проекте
    assert "name=main" in out.reply.explanation_ru


async def test_prompt_step_from_where_the_variant_exists(live) -> None:
    for text in ("удали вариант мастер-промта draft", "убери вариант draft из мастер-промтов"):
        out, fake = await run(text, res(reply([pr("prompt.delete_master", step_code="split", name="draft")])))
        assert out.reply.steps[0]["params"] == {"step_code": "script", "name": "draft"}, text
        assert "step_code" not in sent_context(fake).get("defaults", {})
        assert sent_context(fake)["prompts"]["script"]["names"].count("draft") == 1


async def test_ambiguous_variant_name_asks_for_the_step(live) -> None:
    out, _ = await run("удали вариант мастер-промта main", res(reply([
        pr("prompt.delete_master", step_code="split", name="main")])))
    assert out.reply.steps == [] and "main" in out.reply.needs_clarification
    assert set(out.reply.clarification_options) == {"script", "excel_gpt"}
    out, _ = await run("прочитай мастер-промт main", res(reply([pr("prompt.get_master", name="main")])))
    assert out.reply.steps == [] and out.reply.clarification_options


async def test_selected_and_default_variants_are_not_deleted(live) -> None:
    for text, name in (("удали вариант main мастер-промта шага script", "main"),
                       ("удали вариант default мастер-промта шага script", "default")):
        out, _ = await run(text, res(reply([pr("prompt.delete_master", step_code="script", name=name)])))
        assert out.reply.steps == [] and "удалять его нельзя" in out.reply.needs_clarification, text
        assert "draft" in out.reply.clarification_options
    out, _ = await run("удали вариант draft мастер-промта шага script", res(reply([
        pr("prompt.delete_master", step_code="script", name="draft")])))
    assert out.reply.steps and out.reply.steps[0]["params"]["name"] == "draft"


# --- настройки сборки, покрытие, шоты очереди -------------------------------------------------------

async def test_loudness_fraction_is_converted_and_only_named_settings_are_sent(live) -> None:
    out, fake = await run("сделай громкость музыки 0.2 и включи субтитры в ролике", res(reply([
        st("montage.set_assemble_params", bgm_level=0.2, subtitles_enabled=True)])))
    p = out.reply.steps[0]["params"]
    assert p["bgm_level"] == 20 and p["subtitles_enabled"] is True and "skip_intro_enabled" not in p
    assert "bgm_level=20" in out.reply.explanation_ru
    notes = json.loads(fake.calls[0]["body"]["messages"][1]["content"])["notes"]
    assert "0.2 = 20" in notes["montage.set_assemble_params"]
    out, _ = await run("выключи субтитры в ролике", res(reply([st(
        "montage.set_assemble_params", subtitles_enabled=False)])))
    assert out.reply.steps[0]["params"] == {"project_id": PID, "subtitles_enabled": False}


async def test_coverage_value_is_normalized_and_parent_number_removed(live) -> None:
    out, _ = await run("в кадре 12 сделай общий план", res(reply([st(
        "montage.set_coverage", frame_number=12, type="coverage_plan", value="общий план",
        parent_number=12)])))
    assert out.reply.steps[0]["params"]["value"] == "ОБЩИЙ" and "parent_number" not in out.reply.steps[0]["params"]
    _, fake = await run("добавь движение камеры в кадре 9: наезд", res(reply([st(
        "montage.set_coverage", frame_number=9, type="coverage_move", value="наезд")])))
    sent = json.loads(fake.calls[0]["body"]["messages"][1]["content"])
    assert "наезд" in sent["notes"]["montage.set_coverage"]


async def test_queue_regeneration_asks_only_when_both_shots_have_a_picture(live) -> None:
    ops4 = [{"type": "image_regen", "frame_number": 4}]
    out, _ = await run("поставь кадр 4 на перегенерацию картинки", res(reply([
        st("montage.queue_ops", ops=ops4)])))
    assert out.reply.steps == [] and out.reply.clarification_options == ["шот 1", "шот 2", "оба шота"]
    out, _ = await run("поставь кадр 9 на перегенерацию картинки", res(reply([st(
        "montage.queue_ops", ops=[{"type": "image_regen", "frame_number": 9}])])))
    assert out.reply.steps
    out, _ = await run("поставь кадр 4 на перегенерацию картинки", res(reply([st(
        "montage.queue_ops", ops=[{**ops4[0], "shot": 1}, {**ops4[0], "shot": 2}])])))
    assert len(out.reply.steps[0]["params"]["ops"]) == 2


async def test_prompt_carries_per_action_notes_but_no_server_side_defaults(live) -> None:
    _, fake = await run("сгенерируй картинки для кадров 3, 4 и 5", res(reply([st(
        "montage.generate_scene_images", frame_numbers=[3, 4, 5])])))
    sent = json.loads(fake.calls[0]["body"]["messages"][1]["content"])
    assert "montage.generate_scene_images" in sent["notes"]
    assert "image_generator" not in json.dumps(sent["project_context"])
    assert "Параметры без * необязательны" in fake.calls[0]["body"]["messages"][0]["content"]
