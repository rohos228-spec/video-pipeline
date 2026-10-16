from __future__ import annotations

import json

import pytest

from app.services.action_layer import planner, planner_autofill, planner_catalog, planner_rules as rules
from app.services.action_layer.attachments import synthetic_ref
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.w5_5_heldout_set_test import _placeholder

FACTS = {"project_id": 2,
         "frames": [[7, 1207, "p", 1, "1", "1"], [8, 1208, "c", 1, "1", "-"], [9, 1209, "c", 1, "1", "-"],
                    [11, 1211, "c", 1, "1", "-"]],
         "frame_uuids": {}, "defaults": {},
         "publish_server": {"caption": "Тема проекта", "platforms": ["tiktok", "yt_shorts"]}}


GEN = {"image_generator": "gpt_image_2_vip", "image_resolution": "2k"}


def _reg():
    ensure_builtin_actions()
    return registry


def _reply(steps, clar=None, expl="ok") -> str:
    return json.dumps({"steps": steps, "needs_clarification": clar, "explanation_ru": expl})


def _step(aid: str, params: dict | None = None, deps=None) -> dict:
    return {"action_id": aid, "params": params or {}, "depends_on": deps or []}


def _validate(steps, text, **kw):
    return planner.validate_reply(_reply(steps), _reg(), max_steps=10, project_id=2, facts=FACTS,
                                  request_text=text, **kw)



def test_guard_dropping_every_step_asks_instead_of_raising() -> None:
    out = _validate([_step("montage.delete_frame", {"frame_id": 1207})], "кадр 7 больше не нужен")
    assert out.steps == [] and out.guard_question and not out.model_question
    assert out.needs_clarification.startswith("Вы хотите: удалить кадр 7?")
    assert "Подтвердите" in out.needs_clarification
    assert out.clarification_options == ["Да, подготовить", "Нет, другое"]
    assert out.dropped_steps == ["Удалить кадр"] and out.catalog_steps == []


def test_confirmation_question_names_several_steps_and_key_params() -> None:
    out = _validate([_step("node.run_step", {"node_key": "n_excel_gpt_fw_shots"}),
                     _step("storage.clear_files", {"node_key": "n_excel_gpt_fw_qc"})], "что там с нодами")
    q = out.needs_clarification
    assert out.steps == [] and q.startswith("Вы хотите: запустить шаг на ноде «Работа с GPT» (нода n_excel_gpt_fw_shots);")
    assert "очистить хранилище ноды (нода n_excel_gpt_fw_qc)" in q and "шаги" in q


def test_partial_drop_behaves_as_before() -> None:
    out = _validate([_step("montage.set_assemble_params", {"bgm_level": 20}), _step("montage.run_montage", deps=[0])],
                    "поставь громкость музыки 20")
    assert [s["action_id"] for s in out.steps] == ["montage.set_assemble_params"] and not out.guard_question
    assert "Убрано как не запрошенное" in out.explanation_ru


def _guarded_risky():
    reg = _reg()
    return [s for s in reg.list() if rules.is_guarded(s) and (s.risk in ("paid", "destructive") or s.is_job)]


def test_no_hint_paid_or_destructive_action_never_becomes_an_executable_plan() -> None:
    specs = _guarded_risky()
    assert len(specs) >= 40
    for spec in specs:
        props = spec.params_schema.get("properties") or {}
        params = {"project_id": 2}
        for key in spec.params_schema.get("required") or []:
            params.setdefault(key, _placeholder(props[key]))
        if registry.validate_params(spec.id, params):
            continue
        text = "а как у нас дела"
        if rules.request_hints_action(text, spec):
            continue
        out = _validate([_step(spec.id, params)], text)
        assert out.steps == [] and out.catalog_steps == [], spec.id
        assert out.guard_question and out.needs_clarification.startswith("Вы хотите:"), spec.id


def test_answer_yes_prepares_the_step_and_tells_the_human() -> None:
    steps = [_step("montage.delete_frame", {"frame_id": 1207})]
    out = _validate(steps, "кадр 7 больше не нужен. Уточнение: Да, подготовить")
    assert [s["action_id"] for s in out.steps] == ["montage.delete_frame"] and not out.guard_question
    assert "Подготовлено по вашему ответу «Да, подготовить»" in out.explanation_ru
    assert out.steps[0]["params"]["frame_id"] == 1207


def test_answer_yes_only_counts_at_the_end_of_the_request() -> None:
    steps = [_step("montage.delete_frame", {"frame_id": 1207})]
    for text in ("Уточнение: Да, подготовить. кадр 7 больше не нужен", "кадр 7 нужен, уточнение да подготовить нельзя"):
        out = _validate(steps, text)
        assert out.steps == [] and out.guard_question, text


def test_answer_yes_does_not_lift_the_companion_rule() -> None:
    steps = [_step("project.set_auto_mode", {"enabled": True}), _step("project.continue_pipeline")]
    out = _validate(steps, "хочу чтобы всё шло без меня. Уточнение: Да, подготовить")
    assert [s["action_id"] for s in out.steps] == ["project.set_auto_mode"]


def test_mass_destructive_request_stays_a_question_even_when_every_step_is_dropped() -> None:
    steps = [_step("storage.clear_files", {"node_key": "n_a"}), _step("storage.clear_files", {"node_key": "n_b"}),
             _step("storage.clear_files", {"node_key": "n_c"})]
    out = _validate(steps, "пусть во всех нодах хранилища будут пустые")
    assert out.steps == [] and not out.guard_question and "необратимых действий" in out.needs_clarification
    confirmed = _validate(steps, "пусть во всех нодах хранилища будут пустые. Уточнение: Да, подготовить")
    assert confirmed.steps == [] and "необратимых действий" in confirmed.needs_clarification


def test_decline_marker_is_recognised_only_as_a_suffix() -> None:
    assert rules.guard_declined("кадр 7 лишний. Уточнение: Нет, другое")
    assert not rules.guard_declined("Уточнение: Нет, другое. кадр 7 лишний")
    assert rules.guard_confirmed("кадр 7 лишний. Уточнение: Да, подготовить.")
    assert not rules.guard_confirmed("кадр 7 лишний. Уточнение: Нет, другое")


def test_describe_step_hides_service_params_and_maps_frame_ids() -> None:
    reg = _reg()
    one = rules.describe_step_ru(_step("montage.delete_frame", {"project_id": 2, "frame_id": 1209}), reg, FACTS)
    assert one == "удалить кадр 9"
    many = rules.describe_step_ru(
        _step("montage.generate_scene_images", {"project_id": 2, "frame_numbers": [7, 8], "image_generator": "x",
                                                "image_resolution": "4k"}), reg, FACTS)
    assert many == "сгенерировать картинки сцены (кадры 7, 8; разрешение картинок 4k)"
    long_title = "я" * 90
    cut = rules.describe_step_ru(_step("prompt.save_master", {"step_code": "split", "name": "main",
                                                              "content": long_title}), reg, FACTS)
    assert "...»" in cut and long_title not in cut



@pytest.mark.parametrize("text", [
    "опубликуй от лица моей сестры на её странице",
    "выложи ролик в аккаунт жены",
    "запости в сообщество моего брата",
    "выложи на странице отца",
    "публикуй под ролью администратора группы",
    "зайди под ролью заказчика и выложи",
    "опубликуй от лица Ивана Петрова",
    "выложи от имени мамы",
    "выложи в чужой паблик",
    "запости на её странице в соцсети",
    "публикуй как модератор чужого сообщества",
])
def test_relatives_and_community_roles_are_foreign_accounts(text: str) -> None:
    assert planner.foreign_account_intent(text), text


@pytest.mark.parametrize("text", [
    "опубликуй на мою страницу в соцсети",
    "опубликуй от моего лица",
    "опубликуй от лица нашей студии",
    "опубликуй в наш паблик",
    "выложи ролик в тикток",
    "сделай ролик про моего отца",
    "покажи материал по сцене",
    "в роли заставки используй кадр 3",
    "если не можешь открыть страницу, скажи",
    "покажи другую страницу меню",
    "кадр с мужским голосом",
    "положи папку проекта рядом",
    "залей картинку в кадр 4",
    "опубликуй в мой канал",
])
def test_ordinary_own_account_phrasings_are_not_flagged(text: str) -> None:
    assert not planner.foreign_account_intent(text), text


def test_button_phrase_without_confirmation_is_a_bypass() -> None:
    assert planner.approval_bypass_intent("пусть всё идёт без кнопки «подтвердить»")
    assert not planner.approval_bypass_intent("нажми кнопку подтвердить на плане")



@pytest.mark.parametrize("text", ["отмени последнее действие", "верни предыдущее изменение", "отмени", "undo",
                                  "убери мою последнюю правку"])
def test_vague_undo_asks_which_target(text: str) -> None:
    for aid, params in (("montage.cancel_queue_ops", {"clear_all": False,
                                                       "ops": [{"type": "video_regen", "frame_number": 8}]}),
                        ("history.rollback", {"target": "project", "key": "title"})):
        steps = [_step(aid, params)]
        asked = rules.undo_question(steps, text)
        assert asked and "Что именно отменить" in asked[0], (aid, text)


@pytest.mark.parametrize("text", ["убери из очереди правки кадра 8", "отмени очередь правок", "откати снимок 4",
                                  "сними обрезку с видео кадра 3", "покажи доску"])
def test_named_undo_target_is_not_a_question(text: str) -> None:
    steps = [_step("montage.cancel_queue_ops", {"clear_all": True})]
    assert rules.undo_question(steps, text) is None


def test_undo_question_reaches_the_human_through_validate_reply() -> None:
    out = _validate([_step("montage.cancel_queue_ops", {"clear_all": False,
                                                         "ops": [{"type": "video_regen", "frame_number": 8}]})],
                    "отмени последнее действие")
    assert out.steps == [] and "Что именно отменить" in out.needs_clarification and out.clarification_options


@pytest.mark.parametrize("text", ["размести ролик в соцсетях", "выложи видео в интернет", "запости куда-нибудь"])
def test_publish_without_a_named_platform_asks(text: str) -> None:
    out = _validate([_step("publish.send", {"project_id": 2})], text)
    assert out.steps == [] and "куда опубликовать" in out.needs_clarification.lower()


@pytest.mark.parametrize("text", ["размести ролик в тикток", "выложи видео на все площадки", "опубликуй везде",
                                  "выложи готовое видео", "размести в соцсетях: тикток и ютуб"])
def test_publish_with_a_platform_or_all_platforms_is_still_a_plan(text: str) -> None:
    out = _validate([_step("publish.send", {"project_id": 2})], text)
    assert [s["action_id"] for s in out.steps] == ["publish.send"], text


def test_project_create_without_creation_words_edits_the_open_project() -> None:
    out = _validate([_step("project.create", {"title": "Ночной маршрут", "topic": "лес"})],
                    "название — «Ночной маршрут», тема — лес")
    assert [s["action_id"] for s in out.steps] == ["project.patch_settings"]
    assert out.steps[0]["params"] == {"title": "Ночной маршрут", "topic": "лес", "project_id": 2}
    assert "Выбрано другое действие" in out.explanation_ru
    made = _validate([_step("project.create", {"title": "Ночной маршрут"})], "создай проект «Ночной маршрут»")
    assert [s["action_id"] for s in made.steps] == ["project.create"]
    nothing_open = planner.validate_reply(_reply([_step("project.create", {"title": "Х"})]), _reg(), max_steps=10,
                                          project_id=None, facts={"project_id": None}, request_text="название — «Х»")
    assert [s["action_id"] for s in nothing_open.steps] == ["project.create"]



@pytest.mark.parametrize("words,num", [
    (["пятьдесят", "тысяч"], 50000), (["50", "тысяч"], 50000), (["50к"], 50000), (["двадцать", "пять"], 25),
    (["сто", "двадцать", "тысяч"], 120000), (["тысяча"], 1000), (["полтора"], None), (["абв"], None),
])
def test_russian_numbers(words: list[str], num) -> None:
    assert rules.ru_number(words) == num


@pytest.mark.parametrize("text,want", [
    ("запусти ноду, на вход не больше сорока тысяч токенов", {"max_input_tokens": 40000}),
    ("запусти ноду, лимит на выход 8000 токенов", {"max_output_tokens": 8000}),
    ("запусти ноду, входных максимум 30к токенов", {"max_input_tokens": 30000}),
    ("запусти ноду, не больше сорока тысяч токенов", {}),
    ("запусти ноду, расходуй токены аккуратно", {}),
    ("запусти ноду, на вход подай 40 тысяч токенов", {}),
])
def test_named_token_limits(text: str, want: dict) -> None:
    assert rules.named_token_limits(text) == want


def test_named_token_limit_reaches_the_node_run_step() -> None:
    out = _validate([_step("node.run_step", {"node_key": "n_excel_gpt_fw_shots"})],
                    "запусти ноду n_excel_gpt_fw_shots, на вход не больше сорока тысяч токенов")
    assert out.steps[0]["params"]["max_input_tokens"] == 40000


class _Basis:
    min_requests, max_requests = 2, 5

    def declare(self):
        return {"min_llm_requests": 2, "max_llm_requests": 5, "max_input_tokens": 9000, "max_output_tokens": 4000}


async def test_autofill_keeps_the_named_limit_and_adds_the_missing_ones(monkeypatch) -> None:
    from app.services.action_layer import builtin_wave3 as w3

    async def fake_basis(ctx, params):
        return _Basis()

    monkeypatch.setattr(w3, "_load_basis", fake_basis)
    steps = [{"action_id": "node.run_step", "depends_on": [],
              "params": {"project_id": 2, "node_key": "n_excel_gpt_fw_shots", "max_input_tokens": 40000}}]
    notes = await planner_autofill.fill_node_run_limits(object(), "op", steps)
    p = steps[0]["params"]
    assert p["max_input_tokens"] == 40000 and p["max_output_tokens"] == 4000 and p["min_llm_requests"] == 2
    assert len(notes) == 1


def test_improve_scene_gets_the_named_picture_limit_and_no_stale_default_note() -> None:
    out = _validate([_step("montage.improve_scene", {"frame_id": 1207, "prompt": "лучше", **GEN})],
                    "прокачай сцену кадра 7, до трёх картинок")
    assert out.steps[0]["params"]["max_image_ops"] == 3
    assert "max_image_ops=" not in " ".join(out.applied_defaults)
    plain = _validate([_step("montage.improve_scene", {"frame_id": 1207, "prompt": "лучше", **GEN})], "прокачай сцену кадра 7")
    assert plain.steps[0]["params"]["max_image_ops"] >= 1 and any("max_image_ops" in n for n in plain.applied_defaults)


def test_apply_queue_named_limit_drops_the_stale_default_note() -> None:
    facts = {**FACTS, "queue": {"ops": [[7, "image_regen", 1], [8, "image_regen", 1], [9, "image_regen", 1]]}}
    out = planner.validate_reply(_reply([_step("montage.apply_queue", {})]), _reg(), max_steps=10, project_id=2,
                                 facts=facts, request_text="выполни очередь, не больше двух картинок")
    assert out.steps[0]["params"]["max_image_ops"] == 2
    assert not any(n.startswith("max_image_ops=") for n in out.applied_defaults)



@pytest.mark.parametrize("text,aid", [
    ("проэкт возабнови", "project.resume"),
    ("покажи готовые наборы нод", "group.list"),
    ("вытащи всё из галереи", "montage.recover_outsee"),
    ("картинка переедет в другой слот", "montage.move_image"),
    ("пусть дальше идёт само", "project.set_auto_mode"),
    ("отработай очередь", "montage.apply_queue"),
    ("закончилась очередь или идёт", "montage.apply_status"),
    ("подгони озвучку под паузы", "montage.align_audio"),
    ("заякорь диван", "montage.set_anchors"),
])
def test_synonyms_and_typos_reach_the_right_action_in_the_catalog(text: str, aid: str) -> None:
    sel = planner_catalog.select_catalog(text, {"project_id": 2}, _reg(), 24)
    assert aid in sel.ids, (text, sel.ids)


def test_typo_tolerance_is_a_fallback_and_does_not_pull_in_unrelated_actions() -> None:
    sel = planner_catalog.select_catalog("покажи доску монтажа", {"project_id": 2}, _reg(), 24)
    assert "project.resume" not in sel.ids and "montage.get_board" in sel.ids
    short = planner_catalog.select_catalog("удоли кадр 4", {"project_id": 2}, _reg(), 24)
    assert "montage.delete_frame" in short.ids


@pytest.mark.parametrize("aid,text", [
    ("montage.delete_frame", "дропни кадр 24"),
    ("prompt.delete_master", "избавься от варианта draft в библиотеке шага split"),
    ("montage.align_audio", "подгони озвучку"),
    ("montage.recover_outsee", "вытащи карточки из аутси"),
    ("project.resume", "выходим из паузы"),
    ("montage.split_scene", "всё правее кадра 5 пусть будет новой сценой"),
    ("hitl.reject", "этот запрос 12 мне не подходит"),
    ("montage.generate_scene", "для сцены кадра 3 новый заказ: «заброшенный порт»"),
    ("montage.set_anchors", "заякорь диван"),
    ("project.set_auto_mode", "пусть дальше всё идёт само"),
    ("montage.upload_image", "картинка во второй шот кадра 2"),
    ("hero.replace_image", "основным персонажем станет человек с этого фото"),
])
def test_guard_words_keep_the_clearly_requested_action(aid: str, text: str) -> None:
    spec = _reg().get(aid)
    assert rules.request_hints_action(text, spec), (aid, text)


@pytest.mark.parametrize("aid,text", [
    ("montage.upload_image", "покажи картинку во втором шоте кадра 2"),
    ("montage.delete_frame", "покажи кадр 4"),
    ("project.resume", "что со статусом проекта"),
    ("hitl.reject", "покажи запрос 12"),
    ("montage.recover_outsee", "покажи кадр 4"),
    ("montage.align_audio", "покажи кадр 4"),
])
def test_unrelated_requests_still_do_not_hint_the_action(aid: str, text: str) -> None:
    assert not rules.request_hints_action(text, _reg().get(aid)), (aid, text)


def test_attachment_kind_adds_matching_upload_actions_to_the_catalog() -> None:
    reg = _reg()
    ctx = {"facts": {**FACTS, "defaults": {}}, "version": "v"}
    audio = synthetic_ref(2, "audio", "theme.mp3")
    text = "ну это самое, докинь сюда"
    without = planner.build_prompt(text, ctx, reg, 5, max_catalog=24)[1]["offered"]
    with_file = planner.build_prompt(text, ctx, reg, 5, max_catalog=24, attachments=[audio])[1]["offered"]
    assert "montage.upload_music" in with_file and "montage.upload_voice" in with_file
    assert "montage.upload_music" not in without
    assert "hero.replace_image" not in with_file


def test_picture_noun_and_slot_without_verb_passes_the_guard_only_with_a_slot() -> None:
    out = _validate([_step("montage.upload_image", {"frame_number": 8, "shot": 1})], "картинка в первый шот кадра 8",
                    attachments=[synthetic_ref(2, "image", "pic.png")])
    assert [s["action_id"] for s in out.steps] == ["montage.upload_image"]
    out2 = _validate([_step("montage.upload_image", {"frame_number": 8, "shot": 1})], "картинка бесподобная",
                     attachments=[synthetic_ref(2, "image", "pic.png")])
    assert out2.steps == [] and out2.guard_question



def test_notes_separate_the_close_actions() -> None:
    n = rules.notes_for(["montage.apply_status", "montage.get_cost_basis", "node.get_run_cost_basis",
                         "montage.recover_status", "montage.swap_slots", "montage.move_image",
                         "project.continue_pipeline", "montage.cancel_queue_ops", "node.set_config",
                         "project.create", "group.list"])
    assert "apply_status" in n["montage.get_cost_basis"] and "два шага" in n["montage.get_cost_basis"]
    assert "montage.get_cost_basis" in n["node.get_run_cost_basis"]
    assert "recover_outsee" in n["montage.recover_status"]
    assert "move_image" in n["montage.swap_slots"] and "swap_slots" in n["montage.move_image"]
    assert "set_auto_mode" in n["project.continue_pipeline"]
    assert "сбрось" in n["montage.cancel_queue_ops"] and "вопрос" in n["montage.cancel_queue_ops"]
    assert "checkFix" in n["node.set_config"] and "patch_settings" in n["project.create"]
    assert "наборы нод" in n["group.list"]


def test_video_noun_and_reset_words_reach_their_actions() -> None:
    sel = planner_catalog.select_catalog("ролик кадра 4 мне не нужен", {"project_id": 2}, _reg(), 24)
    assert "montage.delete_video" in sel.ids
    assert rules.request_hints_action("пересчитай всё с нуля", _reg().get("node.reset_from_step"))
    assert not rules.request_hints_action("покажи шаги ноды", _reg().get("node.reset_from_step"))



def test_fixture_edits_after_the_live_run_are_documented() -> None:
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    data = json.loads((root / "tests" / "fixtures" / "action_planner_heldout6.json").read_text(encoding="utf-8"))
    assert "0.797" in data["post_live_edits_note"]
    edited = [c for c in data["cases"] if "post_live_edit" in c]
    assert [c["id"] for c in edited] == ["h6_080"]
    triage = (root / "docs" / "audit" / "PLANNER_SET6_TRIAGE.md").read_text(encoding="utf-8")
    for case in edited:
        note = case["post_live_edit"]
        assert note["reason"].startswith("после живого прогона") and note["was"], case["id"]
        assert case["id"] in triage and case["steps"] == []
    assert "0.797" in triage
    main = json.loads((root / "tests" / "fixtures" / "action_planner_eval.json").read_text(encoding="utf-8"))
    changed = [c for c in main["cases"] if "post_live_edit" in c]
    assert [c["id"] for c in changed] == ["w5_publish_b"] and "w5_publish_b" in triage
