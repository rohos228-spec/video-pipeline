from __future__ import annotations

import json

import pytest

from app.services.action_layer import planner, planner_rules as rules
from app.services.action_layer.attachments import synthetic_ref
from app.services.action_layer.spec import ensure_builtin_actions, registry

UUID3 = "11111111-2222-3333-4444-555555555555"
FACTS = {"project_id": 2, "frames": [[3, 1203, "p", 1, "1", "1"], [4, 1204, "p", 1, "1", "-"]],
         "frame_uuids": {"3": UUID3}, "defaults": {}}


def _reg():
    ensure_builtin_actions()
    return registry


def _kept(aid: str, text: str) -> bool:
    kept, dropped = rules.drop_unrequested(
        [{"action_id": aid, "params": {}, "depends_on": []}], text, _reg())
    return bool(kept) and not dropped



@pytest.mark.parametrize("aid,text", [
    ("montage.improve_scene", "прокачай эту сцену, пусть будет ярче"),
    ("media.resume_poll", "продолжи опрос зависшей задачи видео"),
    ("media.release_unknown", "отпусти ключ clip_007 у провайдера kie, списания нет"),
    ("montage.apply_queue", "выполни очередь правок"),
    ("montage.split_scene", "оторви кадр 9 от сцены"),
    ("montage.explode_scene", "разнеси сцену на одиночные кадры"),
    ("montage.delete_image", "выброси картинку кадра 3"),
    ("montage.delete_video", "стёр бы клип кадра 3"),
    ("montage.upload_music", "закинь этот трек как музыку"),
    ("montage.upload_image", "брось файл в кадр 2"),
    ("hitl.regenerate", "пересоздай результат по запросу 4"),
    ("hero.replace_image", "используй приложенный файл вместо героя"),
    ("project.upload_xlsx", "возьми приложенную таблицу как исходник"),
])
def test_colloquial_verbs_keep_the_action(aid: str, text: str) -> None:
    assert _kept(aid, text), (aid, text)


@pytest.mark.parametrize("aid,text", [
    ("montage.apply_queue", "поставь в очередь перерисовку кадра 4"),
    ("montage.run_montage", "покажи доску"),
    ("media.release_unknown", "покажи статус"),
    ("montage.split_scene", "покажи сцену кадра 3"),
])
def test_unrelated_requests_still_drop_the_risky_action(aid: str, text: str) -> None:
    assert not _kept(aid, text), (aid, text)



def test_kill_switch_without_emergency_words_becomes_project_stop() -> None:
    reg = _reg()
    swap = rules.pick_similar_action("kill_switch.engage", {"project_id": 2, "rationale": "x"},
                                     "останови всё сейчас же", FACTS, None, reg)
    assert swap and swap[0] == "project.stop" and swap[1] == {"project_id": 2}
    for text in ("аварийный стоп, причина: двойное списание", "экстренно останови все платные вызовы",
                 "включи рубильник: утечка ключа"):
        assert rules.pick_similar_action("kill_switch.engage", {"project_id": 2, "rationale": "x"},
                                         text, FACTS, None, reg) is None, text
    assert rules.pick_similar_action("kill_switch.engage", {"project_id": 2, "rationale": "x"},
                                     "останови всё", FACTS, {"kill_switch.engage"}, reg) is None


def test_menu_field_without_menu_words_becomes_coverage() -> None:
    reg = _reg()
    params = {"project_id": 2, "frame_uuid": UUID3, "field": "size", "value": "КРУПНЫЙ"}
    swap = rules.pick_similar_action("shot.edit_field", params, "в кадре 3 крупность поставь крупный",
                                     FACTS, None, reg)
    assert swap and swap[0] == "montage.set_coverage"
    assert swap[1] == {"frame_number": 3, "type": "coverage_plan", "value": "КРУПНЫЙ", "project_id": 2}
    assert reg.validate_params(swap[0], swap[1]) == []
    assert rules.pick_similar_action("shot.edit_field", params, "в меню съёмки у кадра 3 крупность",
                                     FACTS, None, reg) is None
    other = {**params, "field": "action"}
    assert rules.pick_similar_action("shot.edit_field", other, "в кадре 3 действие", FACTS, None, reg) is None


def _reply(steps, clar=None, expl="ok") -> str:
    return json.dumps({"steps": steps, "needs_clarification": clar, "explanation_ru": expl})


def test_validate_reply_swaps_and_explains() -> None:
    reg = _reg()
    out = planner.validate_reply(
        _reply([{"action_id": "kill_switch.engage", "params": {"rationale": "остановить"}, "depends_on": []}]),
        reg, max_steps=5, project_id=2, facts=FACTS, request_text="останови всё немедленно")
    assert [s["action_id"] for s in out.steps] == ["project.stop"]
    assert "другое действие" in out.explanation_ru



def test_hero_mode_question_is_resolved_by_the_server() -> None:
    found = rules.resolve_clarification("Какой режим главного героя выбрать для нового проекта?",
                                        "создай проект «Осень»", FACTS)
    assert {"param": "hero_mode", "value": "auto", "source": "default"} in found
    named = rules.resolve_clarification("Какой режим главного героя выбрать?",
                                        "создай проект с героем «Осень»", FACTS)
    assert all(f["param"] != "hero_mode" for f in named)


def test_deictic_placement_without_a_file_asks_for_the_file() -> None:
    ask = rules.deictic_file_request("положи вот это в кадр 12")
    assert "файл" in ask and "кадр 12" in ask
    assert rules.deictic_file_request("положи эту музыку в проект") == ""
    assert rules.deictic_file_request("поставь этот проект на паузу") == ""
    assert rules.deictic_file_request("покажи кадр 5") == ""


def test_clarification_for_deictic_placement_mentions_the_file() -> None:
    reg = _reg()
    out = planner.validate_reply(
        _reply([], clar="Что именно положить?"), reg, max_steps=5, project_id=2, facts=FACTS,
        request_text="положи вот это в кадр 12")
    assert out.needs_clarification and "файл" in out.needs_clarification
    with_file = planner.validate_reply(
        _reply([], clar="Что именно положить?"), reg, max_steps=5, project_id=2, facts=FACTS,
        request_text="положи вот это в кадр 12",
        attachments=[synthetic_ref(2, "image", "a.png")])
    assert with_file.needs_clarification == "Что именно положить?"



def test_prompt_rules_cover_compound_requests_and_apply_queue_note() -> None:
    assert "Составной запрос" in planner.SYSTEM
    note = rules.ACTION_NOTES["montage.queue_ops"]
    assert "apply_queue" in note and "ТОЛЬКО" in note
    assert _kept("montage.apply_queue", "в очередь перерисовку кадра 3 и выполнить очередь")



@pytest.mark.parametrize("aid,kind,name,params", [
    ("project.upload_xlsx", "excel", "table.xlsx", {}),
    ("hero.replace_image", "image", "hero.png", {}),
    ("storage.upload_file", "text", "notes.txt", {"node_key": "n_excel_gpt_fw_qc"}),
])
def test_wave5_uploads_take_the_attachment_path(aid: str, kind: str, name: str, params: dict) -> None:
    reg = _reg()
    att = synthetic_ref(2, kind, name)
    out = planner.validate_reply(
        _reply([{"action_id": aid, "params": params, "depends_on": []}]), reg, max_steps=5,
        project_id=2, facts=FACTS, request_text="загрузи приложенный файл", attachments=[att])
    assert [s["action_id"] for s in out.steps] == [aid]
    assert out.steps[0]["params"]["source_path"] == att.rel_path


@pytest.mark.parametrize("aid,kind,name", [
    ("project.upload_xlsx", "image", "pic.png"),
    ("hero.replace_image", "audio", "voice.mp3"),
])
def test_wave5_uploads_reject_a_wrong_kind_and_ask_for_a_file_without_one(aid, kind, name) -> None:
    reg = _reg()
    steps = [{"action_id": aid, "params": {}, "depends_on": []}]
    wrong = planner.validate_reply(_reply(steps), reg, max_steps=5, project_id=2, facts=FACTS,
                                   request_text="загрузи файл", attachments=[synthetic_ref(2, kind, name)])
    assert not wrong.steps and "не подходит" in wrong.needs_clarification
    none = planner.validate_reply(_reply(steps), reg, max_steps=5, project_id=2, facts=FACTS,
                                  request_text="загрузи файл")
    assert not none.steps and "Прикрепите файл" in none.needs_clarification


def test_upload_intent_tells_project_table_from_node_file() -> None:
    offered = set(rules.UPLOAD_ACTIONS)
    assert rules.upload_intent_action("залей новую таблицу проекта", offered) == "project.upload_xlsx"
    assert rules.upload_intent_action("загрузи excel файл в ноду n_a", offered) == "node.upload_file"
    assert rules.upload_intent_action("загрузи файл в хранилище ноды n_a", offered) == "storage.upload_file"
    assert rules.upload_intent_action("подмени картинку героя", offered) == "hero.replace_image"
