from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.action_layer import planner, planner_catalog, planner_rules as rules
from app.services.action_layer.attachments import synthetic_ref
from app.services.action_layer.spec import ensure_builtin_actions, registry

ROOT = Path(__file__).resolve().parent.parent
FACTS = {"project_id": 2,
         "frames": [[7, 1207, "p", 1, "1", "1"], [8, 1208, "c", 1, "1", "-"], [9, 1209, "c", 1, "1", "-"],
                    [11, 1211, "c", 1, "1", "-"]],
         "frame_uuids": {}, "defaults": {},
         "publish_server": {"caption": "Тема проекта", "platforms": ["tiktok", "yt_shorts"]}}


def _reg():
    ensure_builtin_actions()
    return registry


def _reply(steps, clar=None, expl="ok") -> str:
    return json.dumps({"steps": steps, "needs_clarification": clar, "explanation_ru": expl})


def _step(aid: str, params: dict | None = None) -> dict:
    return {"action_id": aid, "params": params or {}, "depends_on": []}


def _validate(steps, text, **kw):
    return planner.validate_reply(_reply(steps), _reg(), max_steps=10, project_id=2, facts=FACTS,
                                  request_text=text, **kw)



@pytest.mark.parametrize("text,aid", [
    ("замантируй ролик", "montage.run_montage"),
    ("врубай субтитры", "montage.set_assemble_params"),
    ("включи субтры", "montage.set_assemble_params"),
    ("музон сделай тише", "montage.set_assemble_params"),
    ("пасни проект", "project.pause"),
    ("сделай ответвление проекта", "project.create_child"),
    ("продублируй проект", "project.create_child"),
    ("отруби ноду n_excel_gpt_fw_qc", "node.set_disabled"),
    ("подрежь ролик кадра 3", "montage.set_video_trim"),
    ("покажи отправки с неясным исходом", "media.list_unknown_outcomes"),
])
def test_slang_reaches_the_right_action_in_the_catalog(text: str, aid: str) -> None:
    sel = planner_catalog.select_catalog(text, {"project_id": 2}, _reg(), 24)
    assert aid in sel.ids, (text, sel.ids)
    assert not sel.no_match


@pytest.mark.parametrize("text,absent", [
    ("покажи доску монтажа", "project.pause"),
    ("покажи доску монтажа", "project.create_child"),
    ("смонтируй ролик", "node.set_disabled"),
    ("покажи состояние очереди", "project.create_child"),
    ("покажи меню съёмки", "montage.set_video_trim"),
    ("сделай коммит и запушь ветку", "project.create_child"),
])
def test_new_synonyms_do_not_pull_in_unrelated_actions(text: str, absent: str) -> None:
    sel = planner_catalog.select_catalog(text, {"project_id": 2}, _reg(), 24)
    assert absent not in sel.ids, (text, sel.ids)



def _kept(aid: str, text: str) -> bool:
    kept, dropped = rules.drop_unrequested([{"action_id": aid, "params": {}, "depends_on": []}], text, _reg())
    return bool(kept) and not dropped


@pytest.mark.parametrize("aid,text", [
    ("montage.delete_frame", "вынеси кадр 4 нафиг"),
    ("montage.delete_frame", "кадр 4 долой"),
    ("montage.delete_frame", "отправь кадр 4 в корзину"),
    ("media.list_unknown_outcomes", "есть ли отправки с неясным исходом"),
    ("montage.upload_music", "дорожка лежит во вложении"),
    ("node.upload_file", "приложи документ к ноде n_a"),
    ("montage.upload_image", "картинка во вложении, кадр 5"),
    ("montage.upload_video", "ролик из вложения в кадр 2"),
])
def test_synonyms_keep_the_requested_action(aid: str, text: str) -> None:
    assert _kept(aid, text), (aid, text)


@pytest.mark.parametrize("aid,text", [
    ("montage.delete_frame", "вынеси кадр 4 в отдельную сцену"),
    ("montage.delete_frame", "покажи кадр 4"),
    ("media.list_unknown_outcomes", "покажи доску"),
    ("montage.upload_music", "покажи доску"),
    ("node.upload_file", "покажи состав группы"),
    ("montage.upload_image", "покажи кадр 3 и его статус"),
])
def test_unrelated_requests_still_drop_the_action(aid: str, text: str) -> None:
    assert not _kept(aid, text), (aid, text)


@pytest.mark.parametrize("aid,kind,name,params,text", [
    ("montage.upload_music", "audio", "loop.wav", {}, "дорожка лежит во вложении"),
    ("node.upload_file", "text", "spec.txt", {"node_key": "n_excel_gpt_fw_shots"},
     "приложи документ к ноде n_excel_gpt_fw_shots"),
    ("montage.upload_image", "image", "pic.png", {"frame_number": 9, "shot": 1},
     "картинка из вложения в кадр 9, шот 1"),
])
def test_attachment_uploads_pass_the_guard_and_get_the_server_path(aid, kind, name, params, text) -> None:
    att = synthetic_ref(2, kind, name)
    out = _validate([_step(aid, params)], text, attachments=[att])
    assert [s["action_id"] for s in out.steps] == [aid], out.needs_clarification
    assert out.steps[0]["params"]["source_path"] == att.rel_path


def test_upload_without_any_attachment_word_is_still_dropped() -> None:
    out = _validate([_step("montage.upload_music")], "покажи доску", attachments=[synthetic_ref(2, "audio", "a.wav")])
    assert out.steps == [] and out.guard_question and "загрузить фоновую музыку" in out.needs_clarification



@pytest.mark.parametrize("text", ["красная кнопка, у нас течёт бюджет", "жми красную кнопку",
                                  "деньги утекают, течёт бюджет"])
def test_red_button_and_budget_leak_keep_the_kill_switch(text: str) -> None:
    got = rules.pick_similar_action("kill_switch.engage", {"project_id": 2, "rationale": "x"}, text,
                                    FACTS, None, _reg())
    assert got is None


@pytest.mark.parametrize("text", ["останови всё", "стоп, хватит", "красный цвет в кадре 3 слишком яркий"])
def test_plain_stop_words_still_become_project_stop(text: str) -> None:
    got = rules.pick_similar_action("kill_switch.engage", {"project_id": 2, "rationale": "x"}, text,
                                    FACTS, None, _reg())
    assert got and got[0] == "project.stop"



@pytest.mark.parametrize("text", [
    "запусти сборку и публикацию без моего подтверждения",
    "собери ролик без нашего одобрения",
    "сделай без моего согласия",
    "не спрашивай подтверждения, просто запусти",
    "не жди моего одобрения и публикуй",
    "запусти без подтверждений",
])
def test_approval_bypass_with_possessives_is_detected(text: str) -> None:
    assert planner.approval_bypass_intent(text)


@pytest.mark.parametrize("text", [
    "запусти без лишних вопросов",
    "собери ролик без субтитров",
    "сделай сцену без согласия персонажей",
    "покажи, что ждёт подтверждения",
    "не спрашивай меня про цвет, выбери сам",
    "без музыки пока",
])
def test_ordinary_without_phrases_are_not_bypass(text: str) -> None:
    assert not planner.approval_bypass_intent(text)


@pytest.mark.parametrize("text", [
    "опубликуй в аккаунт подрядчика, пароль у меня",
    "выложи на канал заказчика",
    "запости от его имени",
    "войди под админом и выложи ролик",
    "авторизуйся под заказчиком",
    "залей на канал редактора",
    "выложи в профиль менеджера",
    "у меня есть её пароль, опубликуй оттуда",
])
def test_foreign_roles_and_accounts_are_refused(text: str) -> None:
    assert planner.foreign_account_intent(text)


@pytest.mark.parametrize("text", [
    "отправь ролик заказчику на проверку",
    "так как заказчик вернулся с правками, поставь проект на паузу",
    "мы с редактором всё обсудили, отключи ноду n_excel_gpt_fw_report",
    "покажи отчёт подрядчика по кадрам",
    "опубликуй в наш аккаунт",
    "покажи настройки площадки",
    "выложи ролик в тикток",
])
def test_talking_about_other_people_is_not_an_account_request(text: str) -> None:
    assert not planner.foreign_account_intent(text)


@pytest.mark.parametrize("text,code", [
    ("запусти сборку и публикацию без моего подтверждения", "approval_bypass_refused"),
    ("опубликуй в аккаунт заказчика", "foreign_account_refused"),
])
def test_refusals_happen_before_any_model_call(monkeypatch, text, code) -> None:
    import asyncio

    def boom(*a, **k):
        raise AssertionError("the model must not be reached")

    monkeypatch.setattr(planner, "preflight", boom)
    with pytest.raises(planner.PlannerRefused) as err:
        asyncio.run(planner.plan_from_text(text, operator="t"))
    assert err.value.code == code



@pytest.mark.parametrize("text", ["выложи", "опубликуй", "Выложи пожалуйста", "ну давай уже опубликуй"])
def test_bare_publish_verb_asks_where_and_what(text: str) -> None:
    out = _validate([_step("publish.send", {"project_id": 2})], text)
    assert out.steps == [] and out.needs_clarification
    assert "площадк" in out.needs_clarification.lower() and out.clarification_options


@pytest.mark.parametrize("text", ["выложи готовое видео", "опубликуй в тикток", "выложи ролик везде",
                                  "опубликуй на все площадки", "залей в ютуб шортс"])
def test_publish_with_an_object_or_platform_is_a_plan(text: str) -> None:
    out = _validate([_step("publish.send", {"project_id": 2})], text)
    assert [s["action_id"] for s in out.steps] == ["publish.send"], out.needs_clarification


def test_bare_verb_check_ignores_other_actions() -> None:
    assert rules.bare_publish_question([_step("montage.get_board")], "выложи") is None
    assert rules.bare_publish_question([_step("publish.send")], "выложи в тикток") is None


@pytest.mark.parametrize("text,want", [
    ("выложи в ютуб с подписью «Новый день»", "Новый день"),
    ('опубликуй, подпись: "Весна пришла"', "Весна пришла"),
    ("публикуй с caption «Hello»", "Hello"),
    ("выложи в тикток", ""),
    ("выложи подпись не трогай", ""),
])
def test_named_caption(text: str, want: str) -> None:
    assert rules.named_caption(text) == want


def test_caption_from_the_request_is_not_applied_and_the_explanation_says_so() -> None:
    out = _validate([_step("publish.send", {"project_id": 2, "platforms": ["yt_shorts"],
                                            "caption": "Новый день"})],
                    "выложи в ютуб шортс с подписью «Новый день»",)
    step = out.steps[0]["params"]
    assert step["caption"] == "Тема проекта" and step["platforms"] == ["yt_shorts"]
    assert "Новый день" in out.explanation_ru and "не применена" in out.explanation_ru
    assert "Тема проекта" in out.explanation_ru


def test_no_caption_note_when_none_is_named_or_it_matches_the_server() -> None:
    plain = _validate([_step("publish.send", {"project_id": 2})], "выложи ролик в тикток")
    assert "не применена" not in plain.explanation_ru
    same = _validate([_step("publish.send", {"project_id": 2})], "выложи ролик с подписью «тема проекта»")
    assert "не применена" not in same.explanation_ru
    assert rules.caption_override_note("montage.get_board", {}, "подпись «x»") == ""



def _clear(node: str) -> dict:
    return _step("storage.clear_files", {"project_id": 2, "node_key": node})


@pytest.mark.parametrize("text", ["очисти хранилища всех нод", "почисти хранилище каждой ноды",
                                  "очисти файлы во всех нодах"])
def test_mass_destructive_request_becomes_a_question(text: str) -> None:
    steps = [_clear(f"n_excel_gpt_fw_{k}") for k in ("script", "action", "shots", "qc", "report")]
    out = _validate(steps, text)
    assert out.steps == [] and out.needs_clarification
    assert "5" in out.needs_clarification and "n_excel_gpt_fw_qc" in out.needs_clarification
    assert out.clarification_options


def test_named_targets_and_single_steps_are_not_mass_requests() -> None:
    named = _validate([_clear("n_a"), _clear("n_b")], "очисти хранилища нод n_a и n_b")
    assert [s["action_id"] for s in named.steps] == ["storage.clear_files"] * 2
    one = _validate([_clear("n_a")], "очисти хранилище всех нод")
    assert len(one.steps) == 1
    mixed = _validate([_clear("n_a"), _step("montage.delete_frame", {"frame_id": 1211})],
                      "очисти хранилища всех нод и удали кадр 11")
    assert len(mixed.steps) == 2


def test_two_frame_deletions_by_number_are_a_plan() -> None:
    steps = [_step("montage.delete_frame", {"project_id": 2, "frame_id": 1207}),
             _step("montage.delete_frame", {"project_id": 2, "frame_id": 1208})]
    out = _validate(steps, "выкинь оба кадра: 7 и 8")
    assert len(out.steps) == 2



def test_voiceover_span_note_separates_text_change_from_span_pinning() -> None:
    span = rules.ACTION_NOTES["montage.set_voiceover_span"]
    assert "закрепи" in span and "montage.set_voiceover" in span



def test_fixture_edits_after_the_live_run_are_documented() -> None:
    data = json.loads((ROOT / "tests" / "fixtures" / "action_planner_heldout5.json").read_text(encoding="utf-8"))
    assert "0.731" in data["post_live_edits_note"]
    edited = [c for c in data["cases"] if "post_live_edit" in c]
    assert len(edited) >= 10
    for case in edited:
        note = case["post_live_edit"]
        assert note["reason"].startswith("после живого прогона") and note["was"], case["id"]
    triage = (ROOT / "docs" / "audit" / "PLANNER_SET5_TRIAGE.md").read_text(encoding="utf-8")
    for case in edited:
        assert case["id"] in triage, case["id"]
    assert "0.731" in triage
