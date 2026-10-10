"""Правила вокруг ответа планировщика (planner_rules): умолчания, защита от лишнего, загрузки, шот."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.action_layer import planner, planner_rules as rules
from app.services.action_layer.spec import ensure_builtin_actions, registry

FIXTURE = Path(__file__).parent / "fixtures" / "action_planner_eval.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
PLANS = [c for c in CASES if c["outcome"] == "plan"]

FRAMES = [[1, 1201, "p", 1, "1", "1"], [2, 1202, "c", 2, "12", "1"], [3, 1203, "c", 1, "1", "1"],
          [4, 1204, "p", 2, "12", "12"], [5, 1205, "c", 1, "1", "-"], [6, 1206, "p", 1, "-", "-"]]
FACTS = {
    "project_id": 2, "frames": FRAMES,
    "defaults": {"image_generator": "nano_banana_2", "image_resolution": "2k",
                 "video_generator": "veo_3_fast", "video_resolution": "1080p",
                 "step_code": "split", "prompt_variant": "default"},
    "queue": {"image": 3, "video": 1, "ops": [[3, "image_regen", 1], [3, "image_ai_change", 1],
                                              [4, "image_regen", 1], [8, "video_regen", 1]]},
    "prompt_overrides": {"script": "main"},
}


def schema(aid: str) -> dict:
    ensure_builtin_actions()
    return registry.get(aid).params_schema


def spec(aid: str):
    ensure_builtin_actions()
    return registry.get(aid)


# --- значения по умолчанию ---------------------------------------------------------------

def test_generator_and_resolution_come_from_project_settings() -> None:
    out = rules.fill_defaults("montage.generate_scene_images", {"frame_numbers": [3, 4]}, FACTS,
                              schema("montage.generate_scene_images"))
    assert out.params["image_generator"] == "nano_banana_2" and out.params["image_resolution"] == "2k"
    assert out.params["project_id"] == 2
    assert "image_generator=nano_banana_2" in out.notes
    assert "video_generator" not in out.params  # такого поля у действия нет
    # заданное моделью не перезаписывается
    kept = rules.fill_defaults("montage.generate_scene_images",
                               {"frame_numbers": [3], "image_resolution": "4k"}, FACTS,
                               schema("montage.generate_scene_images"))
    assert kept.params["image_resolution"] == "4k"


def test_apply_queue_limits_follow_the_queue_of_the_named_frames() -> None:
    sch = schema("montage.apply_queue")
    both = rules.fill_defaults("montage.apply_queue", {"frame_numbers": [3, 4]}, FACTS, sch).params
    assert (both["max_image_ops"], both["max_video_ops"]) == (3, 0)
    whole = rules.fill_defaults("montage.apply_queue", {}, FACTS, sch).params
    assert (whole["max_image_ops"], whole["max_video_ops"]) == (3, 1)
    assert whole["video_generator"] == "veo_3_fast"
    given = rules.fill_defaults("montage.apply_queue", {"max_image_ops": 9}, FACTS, sch).params
    assert given["max_image_ops"] == 9


def test_improve_scene_ops_cap_follows_scene_size() -> None:
    out = rules.fill_defaults("montage.improve_scene", {"frame_id": 1204, "prompt": "x"}, FACTS,
                              schema("montage.improve_scene")).params
    assert out["max_image_ops"] == 5  # сцена 4-5: два кадра -> still + по два
    far = rules.fill_defaults("montage.improve_scene", {"frame_id": 999, "prompt": "x"}, FACTS,
                              schema("montage.improve_scene")).params
    assert far["max_image_ops"] == rules.MAX_IMPROVE_OPS


def test_required_shot_is_filled_only_when_it_is_unambiguous() -> None:
    sch = schema("montage.swap_slots")
    one = rules.fill_defaults("montage.swap_slots",
                              {"kind": "image", "a_frame": 3, "b_frame": 5}, FACTS, sch).params
    assert (one["a_shot"], one["b_shot"]) == (1, 1)
    two = rules.fill_defaults("montage.swap_slots",
                              {"kind": "image", "a_frame": 4, "b_frame": 3}, FACTS, sch).params
    assert "a_shot" not in two and two["b_shot"] == 1  # у кадра 4 картинки в обоих шотах
    trim = rules.fill_defaults("montage.set_video_trim", {"frame_number": 3, "start": 1}, FACTS,
                               schema("montage.set_video_trim")).params
    assert trim["shot"] == 1
    amb = rules.fill_defaults("montage.set_video_trim", {"frame_number": 4, "start": 1}, FACTS,
                              schema("montage.set_video_trim")).params
    assert "shot" not in amb


@pytest.fixture
def prompts(tmp_path, monkeypatch):
    """Мастер-промты: draft только у шага script, main у script и excel_gpt (выбран в проекте)."""
    from app.services import prompt_library as pl
    monkeypatch.setattr(pl, "PROMPTS_ROOT", tmp_path)
    for step, names in {"script": ("default", "main", "draft"), "excel_gpt": ("default", "main"),
                        "split": ("default",), "plan": ("default",)}.items():
        d = pl.step_dir(step)
        for n in names:
            (d / f"{n}.md").write_text("x", encoding="utf-8")
    return pl


FACTS_P = {**FACTS, "prompt_overrides": {"script": "main", "excel_gpt": "main"}}


def target(aid, params, text):
    return rules.resolve_prompt_target(aid, params, FACTS_P, text)


def test_prompt_step_comes_from_the_words_first(prompts) -> None:
    # модель взяла step_code из статуса проекта (split), а человек назвал script
    p, _n, ask = target("prompt.get_master", {"step_code": "split", "name": "main"},
                        "прочитай мастер-промт шага script")
    assert ask is None and p["step_code"] == "script" and p["name"] == "main"
    p, notes, ask = target("prompt.get_master", {}, "прочитай мастер-промт шага script")
    assert p == {"step_code": "script", "name": "main"} and notes == ["name=main"]  # выбранный в проекте
    p, _n, _a = target("prompt.get_master", {}, "прочитай мастер-промт шага split")
    assert p["name"] == "default"
    # названо несколько шагов - вопрос
    _p, _n, ask = target("prompt.get_master", {}, "сравни мастер-промт шага script и шага plan")
    assert ask and set(ask[1]) == {"script", "plan"}


def test_prompt_step_comes_from_where_the_named_variant_exists(prompts) -> None:
    for text in ("удали вариант мастер-промта draft", "убери вариант draft из мастер-промтов"):
        p, _n, ask = target("prompt.delete_master", {"step_code": "split", "name": "draft"}, text)
        assert ask is None and p["step_code"] == "script" and p["name"] == "draft", text
    p, _n, ask = target("prompt.get_master", {"name": "draft"}, "покажи мастер-промт draft")
    assert p["step_code"] == "script"


def test_ambiguous_variant_asks_for_the_step_and_never_defaults_to_the_current_one(prompts) -> None:
    p, _n, ask = target("prompt.delete_master", {"step_code": "split", "name": "main"},
                        "удали вариант мастер-промта main")
    assert ask and "main" in ask[0] and set(ask[1]) == {"script", "excel_gpt"}
    _p, _n, ask = target("prompt.get_master", {"step_code": "split", "name": "nothing"},
                         "прочитай мастер-промт nothing")
    assert ask is None and _p["step_code"] == "split"  # такого варианта нет нигде: решает проверка существования
    _p, _n, ask = target("prompt.create_master", {"name": "rough", "content": "x"},
                         "создай вариант мастер-промта rough")
    assert ask and ask[0].startswith("Для какого шага")
    # слово «текущий» разрешает взять шаг проекта
    p, _n, ask = target("prompt.get_master", {"name": "default"}, "прочитай мастер-промт default текущего шага")
    assert ask is None and p["step_code"] == "split"


def test_selected_and_default_variants_cannot_be_deleted(prompts) -> None:
    _p, _n, ask = target("prompt.delete_master", {"name": "main"}, "удали вариант main мастер-промта шага script")
    assert ask and "удалять его нельзя" in ask[0]
    assert "draft" in ask[1] and "main" not in ask[1] and "default" not in ask[1]
    _p, _n, ask = target("prompt.delete_master", {"name": "default"}, "удали вариант default шага split")
    assert ask and "удалять его нельзя" in ask[0]
    p, _n, ask = target("prompt.delete_master", {"name": "draft"}, "удали вариант draft шага script")
    assert ask is None and p["step_code"] == "script"
    # не промты: ничего не меняется
    same = {"node_key": "x"}
    assert rules.resolve_prompt_target("node.run_step", same, FACTS_P, "запусти") == (same, [], None)


def test_loudness_fraction_maps_to_the_0_100_scale_with_a_note() -> None:
    sch = schema("montage.set_assemble_params")
    out = rules.fill_defaults("montage.set_assemble_params", {"bgm_level": 0.2, "subtitles_enabled": True},
                              FACTS, sch)
    assert out.params["bgm_level"] == 20 and isinstance(out.params["bgm_level"], int)
    assert any("bgm_level=20" in n for n in out.notes)
    for given in (30, 1, 0, 100):  # уже шкала 0..100: не трогаем
        got = rules.fill_defaults("montage.set_assemble_params", {"bgm_level": given}, FACTS, sch)
        assert got.params["bgm_level"] == given and not got.notes


def test_coverage_values_are_normalized_and_stray_parent_number_removed() -> None:
    sch = schema("montage.set_coverage")
    out = rules.fill_defaults("montage.set_coverage", {
        "frame_number": 12, "type": "coverage_plan", "value": "общий план", "parent_number": 12}, FACTS, sch)
    assert out.params["value"] == "ОБЩИЙ" and "parent_number" not in out.params
    out = rules.fill_defaults("montage.set_coverage", {
        "frame_number": 9, "type": "coverage_move", "value": "наезд"}, FACTS, sch).params
    assert out["value"] == "наезд"
    out = rules.fill_defaults("montage.set_coverage", {
        "frame_number": 6, "type": "coverage_angle", "value": "вид сверху"}, FACTS, sch).params
    assert out["value"] == "сверху"
    kept = rules.fill_defaults("montage.set_coverage", {
        "frame_number": 6, "type": "coverage_kind", "value": "child", "parent_number": 4}, FACTS, sch).params
    assert kept["parent_number"] == 4 and kept["value"] == "child"
    odd = rules.fill_defaults("montage.set_coverage", {
        "frame_number": 6, "type": "coverage_light", "value": "контражур"}, FACTS, sch).params
    assert odd["value"] == "контражур"  # не из списка: не угадываем


def test_queue_ops_without_shot_asks_when_both_shots_have_files() -> None:
    ops = [{"type": "image_regen", "frame_number": 4}]
    ask = rules.shot_ambiguity("montage.queue_ops", {"ops": ops}, FACTS)
    assert ask and "шот" in ask[0] and ask[1][-1] == "оба шота"
    assert rules.shot_ambiguity("montage.queue_ops", {"ops": [{**ops[0], "shot": 1}]}, FACTS) is None
    assert rules.shot_ambiguity("montage.queue_ops",
                                {"ops": [{"type": "image_regen", "frame_number": 3}]}, FACTS) is None
    # видео кадра 4 тоже в обоих шотах, у кадра 2 видео только в шоте 1
    assert rules.shot_ambiguity("montage.queue_ops",
                                {"ops": [{"type": "video_regen", "frame_number": 4}]}, FACTS)
    assert rules.shot_ambiguity("montage.queue_ops",
                                {"ops": [{"type": "video_regen", "frame_number": 2}]}, FACTS) is None


def test_notes_are_per_offered_action_and_cover_registered_actions() -> None:
    ensure_builtin_actions()
    assert set(rules.ACTION_NOTES) <= {s.id for s in registry.list()}
    n = rules.notes_for(["montage.get_board", "montage.set_coverage", "montage.upload_image",
                         "montage.upload_voice"])
    assert set(n) == {"montage.set_coverage", "upload.*"}  # загрузки одной строкой
    assert "наезд" in n["montage.set_coverage"] and rules.notes_for(["montage.get_board"]) == {}
    assert sum(len(v) for v in rules.ACTION_NOTES.values()) < 8200


def test_no_defaults_without_a_project() -> None:
    out = rules.fill_defaults("montage.apply_queue", {}, {"project_id": None}, schema("montage.apply_queue"))
    assert out.params == {} and out.notes == []


def test_defaults_used_reports_values_not_named_in_the_request() -> None:
    p = {"image_generator": "nano_banana_2", "image_resolution": "4k", "frame_numbers": [3]}
    notes = rules.defaults_used("montage.generate_scene_images", p, FACTS, "сгенерируй картинки в 4k")
    assert notes == ["image_generator=nano_banana_2"]
    assert rules.defaults_used("montage.generate_scene_images", p, FACTS, "на nano_banana_2 в 4k") == []
    assert "По умолчанию подставлено: a=1, b=2." in rules.with_defaults_note("Делаю.", ["a=1", "b=2", "a=1"])
    assert rules.with_defaults_note("Делаю.", []) == "Делаю."


def test_the_prompt_keeps_the_rules_that_stop_the_model_asking_about_optional_params() -> None:
    # правила для модели лежат в SYSTEM: проверяем, что они там есть (иначе правило потеряно)
    for needle in ("Параметры без * необязательны", "defaults", "clarification_options",
                   "Делай только названное", "notes"):
        assert needle in planner.SYSTEM, needle


# --- шот при удалении --------------------------------------------------------------------

def test_delete_without_shot_asks_only_when_both_shots_have_files() -> None:
    ask = rules.shot_ambiguity("montage.delete_image", {"frame_number": 4}, FACTS)
    assert ask and "шот" in ask[0] and ask[1] == ["шот 1", "шот 2"]
    assert rules.shot_ambiguity("montage.delete_video", {"frame_number": 4}, FACTS)
    assert rules.shot_ambiguity("montage.delete_image", {"frame_number": 3}, FACTS) is None
    assert rules.shot_ambiguity("montage.delete_image", {"frame_number": 4, "shot": 2}, FACTS) is None
    assert rules.shot_ambiguity("montage.delete_frame", {"frame_id": 1}, FACTS) is None
    assert rules.shot_ambiguity("montage.delete_image", {"frame_number": 4}, None) is None


# --- защита от лишнего -------------------------------------------------------------------

def step(aid: str, deps=None, **params) -> dict:
    return {"action_id": aid, "params": {"project_id": 2, **params}, "depends_on": deps or []}


def test_unrequested_run_montage_is_dropped_but_requested_one_stays() -> None:
    ensure_builtin_actions()
    plan = [step("montage.set_assemble_params", bgm_level=20, subtitles_enabled=True),
            step("montage.run_montage", deps=[0])]
    kept, dropped = rules.drop_unrequested(
        plan, "сделай громкость музыки 0.2 и включи субтитры в ролике", registry)
    assert [s["action_id"] for s in kept] == ["montage.set_assemble_params"]
    assert dropped == [spec("montage.run_montage").title_ru]
    kept, dropped = rules.drop_unrequested(plan, "поставь громкость музыки 20 и смонтируй ролик", registry)
    assert len(kept) == 2 and dropped == []


def test_dependents_of_a_dropped_step_go_with_it_and_indexes_are_remapped() -> None:
    ensure_builtin_actions()
    plan = [step("montage.get_board"), step("montage.apply_queue", deps=[0], max_image_ops=1, max_video_ops=0),
            step("montage.get_cost_basis", deps=[1]), step("montage.get_board", deps=[0])]
    kept, dropped = rules.drop_unrequested(plan, "покажи доску и цены", registry)
    assert [s["action_id"] for s in kept] == ["montage.get_board", "montage.get_board"]
    assert kept[1]["depends_on"] == [0] and len(dropped) == 2


def test_apply_is_not_added_to_a_queue_request_but_is_kept_when_asked() -> None:
    ensure_builtin_actions()
    plan = [step("montage.queue_ops", ops=[{"type": "image_regen", "frame_number": 4}]),
            step("montage.apply_queue", deps=[0], max_image_ops=1, max_video_ops=0)]
    kept, _ = rules.drop_unrequested(plan, "поставь кадр 4 на перегенерацию картинки", registry)
    assert [s["action_id"] for s in kept] == ["montage.queue_ops"]
    kept, _ = rules.drop_unrequested(plan, "поставь кадр 4 на перегенерацию и примени очередь", registry)
    assert len(kept) == 2


def test_unknown_actions_are_never_dropped_by_the_guard() -> None:
    ensure_builtin_actions()

    class Spec:
        id = "zz.new_paid"
        risk = "paid"
        is_job = False
        title_ru = "Новое"

    assert not rules.is_guarded(Spec())
    assert rules.request_hints_action("что угодно", Spec()) is False


def test_every_risky_builtin_action_has_explicit_guard_hints() -> None:
    ensure_builtin_actions()
    assert rules.unguarded_risky(registry) == []


@pytest.mark.parametrize("case", PLANS, ids=[c["id"] for c in PLANS])
def test_guard_keeps_every_step_the_fixture_expects(case) -> None:
    ensure_builtin_actions()
    plan = [{"action_id": s["action_id"], "params": {}, "depends_on": []} for s in case["steps"]]
    kept, dropped = rules.drop_unrequested(plan, case["text"], registry)
    assert dropped == [] and len(kept) == len(plan), (case["id"], dropped)


# --- загрузки ------------------------------------------------------------------------------

def test_upload_clarification_names_the_action_and_the_target() -> None:
    up = rules.upload_clarification("montage.upload_image",
                                    {"project_id": 2, "frame_number": 5, "shot": 2,
                                     "source_path": "x", "data_base64": "y", "filename": "a.png"},
                                    "Загрузить картинку в слот кадра")
    assert up["question"].startswith("Прикрепите файл кнопкой загрузки")
    assert "Загрузить картинку в слот кадра" in up["question"] and "кадр 5, шот 2" in up["question"]
    assert up["options"] == ["Прикрепить файл"]
    assert up["attachment"]["action_id"] == "montage.upload_image"
    assert up["attachment"]["params"] == {"frame_number": 5, "shot": 2}  # без полей файла и project_id
    node = rules.upload_clarification("node.upload_file", {"node_key": "n_x"}, "Загрузить файл в ноду")
    assert "нода n_x" in node["question"]


@pytest.mark.parametrize("aid", sorted(rules.UPLOAD_ACTIONS))
def test_every_upload_action_is_covered_and_registered(aid) -> None:
    ensure_builtin_actions()
    assert registry.find(aid) is not None
    assert aid in rules.GUARD_HINTS and aid in rules._FORMATS


def test_upload_intent_picks_the_right_action_by_the_noun() -> None:
    offered = set(rules.UPLOAD_ACTIONS)
    for text, want in (("загрузи озвучку с компьютера", "montage.upload_voice"),
                       ("загрузи фоновую музыку", "montage.upload_music"),
                       ("загрузи свою картинку в слот кадра 5", "montage.upload_image"),
                       ("загрузи видео файл в кадр 6", "montage.upload_video"),
                       ("загрузи реф для кадра 3", "montage.upload_ref"),
                       ("загрузи агента проверки в ноду n_a", "node.upload_check_agent"),
                       ("загрузи excel файл в ноду n_a", "node.upload_file"),
                       ("хочу залить свою озвучку", "montage.upload_voice")):
        assert rules.upload_intent_action(text, offered) == want, text
    assert rules.upload_intent_action("поменяй озвучку кадра 5", offered) is None
    assert rules.upload_intent_action("загрузи озвучку", {"montage.get_board"}) is None
    assert rules.upload_params_from_text("montage.upload_image", "картинку в кадр 5") == {"frame_number": 5}
    assert rules.upload_params_from_text("node.upload_file", "в ноду n_excel_gpt_fw_qc") == {
        "node_key": "n_excel_gpt_fw_qc"}


def test_clean_options_are_short_unique_and_limited() -> None:
    raw = ["шот 1", "шот 1", "", "  ", 5, "x" * 200, "б", "в", "г", "д"]
    out = rules.clean_options(raw)
    assert out[0] == "шот 1" and len(out) == rules.MAX_OPTIONS
    assert all(len(o) <= rules.MAX_OPTION_LEN for o in out)
    assert rules.clean_options(None) == [] and rules.clean_options("шот") == []


def test_notes_steer_eval9_failures() -> None:
    ensure_builtin_actions()
    n = rules.notes_for(["group.get", "group.list", "shot.add_cell", "shot.edit_field",
                         "montage.set_coverage", "node.clear_check_agent"])
    assert "group.get" in n["group.list"] and "group_id" in n["group.get"]
    assert "shot.edit_field" in n["montage.set_coverage"] or "shot.edit_field" in n["shot.edit_field"]
    assert "set_coverage" in n["shot.edit_field"] and "конец" in n["shot.add_cell"]
    assert "вопрос" in n["node.clear_check_agent"]
    assert sum(len(v) for v in rules.ACTION_NOTES.values()) < 8200


def test_db_frame_id_in_a_frame_number_param_is_turned_back_into_the_number() -> None:
    facts = {**FACTS, "max_frame_number": 6}
    f = rules.fill_defaults("montage.move_image", {"from_frame": 1205, "to_frame": 1203},
                            facts, schema("montage.move_image"))
    assert (f.params["from_frame"], f.params["to_frame"]) == (5, 3)
    assert any("1205" in n for n in f.notes)
    g = rules.fill_defaults("montage.apply_queue", {"frame_numbers": [1203, 4]}, facts,
                            schema("montage.apply_queue"))
    assert g.params["frame_numbers"] == [3, 4]


def test_legit_frame_number_is_not_mistaken_for_an_id() -> None:
    # номера совпадают с чужими id (id 1..6 у других кадров): настоящий номер не больше максимального
    rows = [[1, 3, "p", 1, "1", "1"], [2, 1, "c", 1, "1", "1"], [3, 2, "c", 1, "1", "1"]]
    facts = {**FACTS, "frames": rows, "max_frame_number": 3}
    f = rules.fill_defaults("montage.move_image", {"from_frame": 2, "to_frame": 3}, facts,
                            schema("montage.move_image"))
    assert (f.params["from_frame"], f.params["to_frame"]) == (2, 3)
    # неизвестное большое число (не id) остаётся как есть и отклоняется строгой проверкой позже
    h = rules.fill_defaults("montage.move_image", {"from_frame": 9999, "to_frame": 3}, facts,
                            schema("montage.move_image"))
    assert h.params["from_frame"] == 9999


# --- набор №8: разговорные синонимы и разрешение из фразы ------------------------------------------------

@pytest.mark.parametrize("action_id,text", [
    ("montage.delete_child_frame", "шот 26 внутри сцены не нужен, вычёркивай его"),
    ("montage.delete_frame", "29-й кадр вырежи из ролика целиком"),
    ("montage.delete_frame", "24-й кадр — в утиль"),
    ("montage.delete_image", "в шестом кадре ни картинки, ни клипа не оставляй"),
    ("node.reset_from_step", "результаты script и всех последующих шагов зачисти"),
    ("prompt.save_master", "перезапиши main у шага split текстом «Режь на короткие блоки»"),
    ("hero.replace_image", "картинку героя надо поменять на мою"),
])
def test_colloquial_synonyms_count_as_a_request_for_the_action(action_id, text):
    ensure_builtin_actions()
    assert rules.request_hints_action(text, registry.find(action_id)) is True


def test_colloquial_words_do_not_unlock_unrelated_destructive_actions():
    ensure_builtin_actions()
    assert rules.request_hints_action("покажи кадр 5", registry.find("montage.delete_frame")) is False


@pytest.mark.parametrize("text,expected", [
    ("по сценам для 10-го и 11-го кадров сделай картинки, разрешение 4к", "4k"),
    ("картинки первых трёх кадров сгенерируй в 3к", "3k"),
])
def test_resolution_named_in_the_request_overrides_the_default(text, expected):
    out = rules.apply_request_values("montage.generate_scene_images", {"image_resolution": "2k"}, text)
    assert out["image_resolution"] == expected


def test_resolution_is_left_alone_when_the_request_names_none_or_two():
    base = {"image_resolution": "2k"}
    assert rules.apply_request_values("montage.generate_scene_images", base, "сделай картинки кадров 2 к 5")["image_resolution"] == "2k"
    assert rules.apply_request_values("montage.generate_scene_images", base, "кадры в 3к, потом в 4к")["image_resolution"] == "2k"


# --- набор №9: потолок кредитов и «после какой ноды» из фразы -------------------------------------------

@pytest.mark.parametrize("action_id,text,expected", [
    ("step.run_step", "прогони шаг картинок, трать не больше 40 кредитов", 40),
    ("project.resume", "выведи проект из паузы, потолок 12,5 кредита", 12.5),
    ("step.run_step", "шаг video запусти, лимит 8 кр", 8),
])
def test_named_credit_ceiling_is_kept(action_id, text, expected):
    assert rules.apply_request_values(action_id, {"step_code": "img"}, text)["ceiling_credits"] == expected


def test_credit_ceiling_is_not_invented_or_overwritten():
    assert "ceiling_credits" not in rules.apply_request_values("step.run_step", {}, "прогони шаг картинок")
    assert "ceiling_credits" not in rules.apply_request_values("step.run_step", {}, "кадры 3 и 5 прогони, кредитов осталось мало")
    assert rules.apply_request_values("step.run_step", {"ceiling_credits": 7}, "не больше 40 кредитов")["ceiling_credits"] == 7
    assert "ceiling_credits" not in rules.apply_request_values("montage.delete_frame", {}, "удали 40 кредитов")


def test_group_insert_keeps_the_named_node_to_insert_after():
    out = rules.apply_request_values("group.insert", {"group_id": "g"}, "вставь группу g после n_excel_gpt_fw_split")
    assert out["after"] == "n_excel_gpt_fw_split"
    out = rules.apply_request_values("group.insert", {"group_id": "g"}, "в канвас, сразу за n_excel_gpt_fw_script, добавь g")
    assert out["after"] == "n_excel_gpt_fw_script"
    assert "after" not in rules.apply_request_values("group.insert", {"group_id": "g"}, "вставь группу g")


@pytest.mark.parametrize("action_id,text", [
    ("media.get_recovery_assessment", "оцени, что стряслось с видео у провайдера"),
    ("montage.align_audio", "пересчитай привязку озвучки по паузам"),
    ("hero.replace_image", "герой теперь выглядит так, обнови его картинку"),
    ("montage.upload_image", "для 3 кадра картинка в аттаче"),
])
def test_set9_words_count_as_a_request_for_the_action(action_id, text):
    ensure_builtin_actions()
    assert rules.request_hints_action(text, registry.find(action_id)) is True


# --- набор №10: отказы по имени человека и «без предварительного просмотра» ----------------------------

@pytest.mark.parametrize("text", [
    "залогинься под аккаунтом Марины и опубликуй с него",
    "публикуй от имени Ивана",
    "выложи ролик на канал Сергея",
])
def test_account_named_by_a_persons_name_is_refused(text):
    assert planner.foreign_account_intent(text) is True


@pytest.mark.parametrize("text", [
    "опубликуй на канал Ютуба",
    "выложи в аккаунт Инстаграма",
    "опубликуй от имени Студии",
    "опубликуй в инстаграм и тикток",
])
def test_platforms_and_own_entities_are_not_a_persons_account(text):
    assert planner.foreign_account_intent(text) is False


def test_publishing_without_a_preview_is_a_bypass_but_showing_frames_is_not():
    assert planner.approval_bypass_intent("опубликуй всё без предварительного просмотра") is True
    assert planner.approval_bypass_intent("покажи кадры без просмотра") is False
